from __future__ import annotations

import sys
from pathlib import Path

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
