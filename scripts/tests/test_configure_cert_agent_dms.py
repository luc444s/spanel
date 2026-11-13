from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import configure_cert_agent_dms as configure


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "mail-config"
    (root / "postfix").mkdir(parents=True)
    (root / "postfix-main.cf").write_text(
        "tls_server_sni_maps = hash:/tmp/docker-mailserver/postfix/sni-old\n",
        encoding="utf-8",
    )
    (root / "postfix/sni-old").write_text(
        "mail.legacy.test /legacy.pem\nmail.managed.test /managed.pem\n",
        encoding="utf-8",
    )
    (root / "dovecot.cf").write_text(
        "local_name mail.legacy.test {\n  ssl_cert = </legacy.crt\n}\n\n"
        "local_name mail.managed.test {\n  ssl_cert = </managed.crt\n}\n",
        encoding="utf-8",
    )
    agent = tmp_path / "cert-agent.toml"
    agent.write_text('allowlist = ["mail.managed.test"]\n', encoding="utf-8")
    return root, agent


def test_plan_separates_managed_and_preserves_legacy(tmp_path: Path) -> None:
    root, agent = _fixture(tmp_path)

    plan = configure.build_plan(
        container="mailserver",
        config_root=root,
        agent_config=agent,
    )

    assert plan.managed_hostnames == ("mail.managed.test",)
    assert plan.legacy_hostnames == ("mail.legacy.test",)
    assert "mail.managed.test" not in plan.writes[root / "postfix/sni-old"]
    assert "mail.legacy.test" in plan.writes[root / "postfix/sni-old"]
    assert "mail.managed.test" in plan.writes[root / "postfix/sni-spanel"]
    assert "mail.legacy.test" in plan.writes[root / "dovecot.cf"]
    assert "mail.managed.test" not in plan.writes[root / "dovecot.cf"]
    assert configure.DOVECOT_INCLUDE in plan.writes[root / "dovecot.cf"]
    assert "mail.managed.test" in plan.writes[root / "spanel-local_name.conf"]
    assert "[postfix]" in plan.writes[agent]
    assert "[dovecot]" in plan.writes[agent]
    assert '"postmap", "-F"' in plan.writes[agent]


def test_plan_is_idempotent_after_writes(tmp_path: Path) -> None:
    root, agent = _fixture(tmp_path)
    first = configure.build_plan(
        container="mailserver",
        config_root=root,
        agent_config=agent,
    )
    for path, content in first.writes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    second = configure.build_plan(
        container="mailserver",
        config_root=root,
        agent_config=agent,
    )

    assert all(
        path.read_text(encoding="utf-8") == content
        for path, content in second.writes.items()
    )


def test_toml_section_update_does_not_duplicate() -> None:
    original = '[postfix]\nsni_map_path = "/old"\n\n[dovecot]\nlocal_name_path = "/x"\n'
    updated = configure._upsert_toml_section(
        original, "postfix", 'sni_map_path = "/new"'
    )

    assert updated.count("[postfix]") == 1
    assert 'sni_map_path = "/new"' in updated
    assert "[dovecot]" in updated


def test_compose_argv_recreates_only_the_service() -> None:
    argv = configure._compose_argv(
        "mail", "mailserver", ["/opt/stacks/mail/docker-compose.yml"]
    )

    assert argv[:3] == ["docker", "compose", "--project-name"]
    assert "--file" in argv
    assert "--force-recreate" in argv
    assert argv[-1] == "mailserver"


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_compose_labels_reads_container_json(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "mail",
                    "com.docker.compose.service": "mailserver",
                    "com.docker.compose.project.config_files": (
                        "/opt/stacks/mail/docker-compose.yml"
                    ),
                }
            }
        }
    ]
    monkeypatch.setattr(
        configure, "_run", lambda *args, **kwargs: _Result(json.dumps(payload))
    )

    assert configure._compose_labels("mailserver") == (
        "mail",
        "mailserver",
        ["/opt/stacks/mail/docker-compose.yml"],
    )


def test_compose_labels_falls_back_to_working_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [
        {
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "mail",
                    "com.docker.compose.service": "mailserver",
                    "com.docker.compose.project.working_dir": "/opt/stacks/mail",
                }
            }
        }
    ]
    monkeypatch.setattr(
        configure, "_run", lambda *args, **kwargs: _Result(json.dumps(payload))
    )

    assert configure._compose_labels("mailserver")[2] == [
        "/opt/stacks/mail/docker-compose.yml"
    ]


def test_compose_labels_rejects_plain_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"Config": {"Labels": {}}}]
    monkeypatch.setattr(
        configure, "_run", lambda *args, **kwargs: _Result(json.dumps(payload))
    )

    with pytest.raises(configure.ConfigureError):
        configure._compose_labels("mailserver")


def test_wait_for_postfix_retries_until_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(
        [
            _Result(returncode=1),
            _Result(""),
            _Result(returncode=1),
            _Result(""),
            _Result(returncode=0),
            _Result("hash:/tmp/docker-mailserver/postfix/sni-spanel"),
        ]
    )
    monkeypatch.setattr(configure, "_run", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(configure.time, "sleep", lambda _seconds: None)

    configure._wait_for_postfix("mailserver")


def test_root_keys_are_written_before_sections() -> None:
    original = 'allowlist = ["mail.example.test"]\n\n[postfix]\nsni_map_path = "/x"\n'

    updated = configure._upsert_root_keys(
        original, {"key_mode": '"640"', "key_group": "107"}
    )

    assert 'key_mode = "640"' in updated
    assert "key_group = 107" in updated
    assert updated.index("key_group") < updated.index("[postfix]")


def test_root_keys_replace_existing_values() -> None:
    original = 'key_mode = "600"\n\n[dovecot]\nlocal_name_path = "/x"\n'

    updated = configure._upsert_root_keys(original, {"key_mode": '"640"'})

    assert 'key_mode = "640"' in updated
    assert '"600"' not in updated
