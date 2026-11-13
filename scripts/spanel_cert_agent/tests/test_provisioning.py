"""Auto-provisioning of allowlist and Traefik ACME routing."""

from __future__ import annotations

from pathlib import Path

from spanel_cert_agent.config import (
    AgentConfig,
    CertbotConfig,
    DovecotConfig,
    PostfixConfig,
)
from spanel_cert_agent.provisioning import (
    ensure_allowlist,
    ensure_traefik_acme,
    parse_acme_router_hosts,
    render_acme_router,
    upsert_allowlist,
)


def base_config(tmp_path: Path, **overrides) -> AgentConfig:
    values = {
        "allowlist": frozenset({"mail.systutor.com"}),
        "source": str(tmp_path / "cert-agent.toml"),
        "traefik_config_dir": tmp_path / "traefik",
        "traefik_acme_router_file": "spanel-acme-mail-challenge.yml",
        "certbot": CertbotConfig(lineage_root=tmp_path / "lineage"),
        "postfix": PostfixConfig(
            sni_map_path=tmp_path / "sni",
            postmap_command=("postmap", "sni"),
            reload_command=("postfix", "reload"),
        ),
        "dovecot": DovecotConfig(
            local_name_path=tmp_path / "dovecot.conf",
            reload_command=("dovecot", "reload"),
        ),
    }
    values.update(overrides)
    return AgentConfig(**values)


def test_upsert_allowlist_replaces_and_appends() -> None:
    text = 'allowlist = ["mail.a.test"]\n\n[certbot]\nexecutable = "x"\n'
    updated = upsert_allowlist(text, ["mail.a.test", "mail.b.test"])
    assert 'allowlist = [\n  "mail.a.test",\n  "mail.b.test",\n]' in updated
    assert "[certbot]" in updated


def test_ensure_allowlist_persists_and_updates_memory(tmp_path: Path) -> None:
    config = base_config(tmp_path)
    Path(config.source).write_text('allowlist = ["mail.systutor.com"]\n', encoding="utf-8")

    updated = ensure_allowlist(config, "mail.other.com")

    assert "mail.other.com" in updated.allowlist
    assert "mail.other.com" in Path(config.source).read_text(encoding="utf-8")


def test_ensure_allowlist_works_in_memory_without_source_file(tmp_path: Path) -> None:
    config = base_config(tmp_path, source=str(tmp_path / "missing.toml"))

    updated = ensure_allowlist(config, "mail.other.com")

    assert "mail.other.com" in updated.allowlist


def test_ensure_traefik_acme_writes_and_merges(tmp_path: Path) -> None:
    config = base_config(tmp_path)
    config.traefik_config_dir.mkdir(parents=True)

    changed = ensure_traefik_acme(config, "mail.a.test", reload_delay=0)
    assert changed is True
    path = config.traefik_config_dir / config.traefik_acme_router_file
    assert "mail.a.test" in path.read_text(encoding="utf-8")

    changed = ensure_traefik_acme(config, "mail.b.test", reload_delay=0)
    assert changed is True
    hosts = parse_acme_router_hosts(path.read_text(encoding="utf-8"))
    assert hosts == ["mail.a.test", "mail.b.test"]

    changed = ensure_traefik_acme(config, "mail.b.test", reload_delay=0)
    assert changed is False


def test_ensure_traefik_acme_is_noop_when_dir_missing(tmp_path: Path) -> None:
    config = base_config(tmp_path, traefik_config_dir=tmp_path / "no-traefik")
    assert ensure_traefik_acme(config, "mail.a.test", reload_delay=0) is False


def test_render_acme_router_is_self_contained(tmp_path: Path) -> None:
    text = render_acme_router(["mail.a.test"])
    assert "acme-webroot:80" in text
    assert "Host(`mail.a.test`)" in text
