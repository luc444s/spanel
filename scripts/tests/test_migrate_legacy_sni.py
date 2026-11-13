"""Unit tests for `migrate_legacy_sni.py` (no Docker, no network)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import migrate_legacy_sni as migrate


def _settings(tmp_path: Path, allowlist: list[str]) -> migrate.AgentSettings:
    return migrate.AgentSettings(
        path=tmp_path / "cert-agent.toml",
        text="",
        allowlist=tuple(allowlist),
        install_root=tmp_path / "installed",
        lineage_root=tmp_path / "live",
        certbot="/usr/bin/certbot",
        challenge="webroot",
        webroot=tmp_path / "webroot",
        postfix_map=None,
        postmap_command=(),
        reload_command=(),
    )


def _entry(hostname: str) -> migrate.SniEntry:
    return migrate.SniEntry(
        hostname=hostname,
        key_path="/legacy.key",
        cert_path="/legacy.pem",
        raw=f"{hostname} /legacy.key /legacy.pem",
    )


def test_parse_sni_map_ignores_comments_and_incomplete_lines() -> None:
    text = (
        "# legacy map\n"
        "mail.a.test  /k/a  /c/a\n"
        "\n"
        "  mail.b.test   /k/b   /c/b  \n"
        "incomplete\n"
    )

    entries = migrate.parse_sni_map(text)

    assert [entry.hostname for entry in entries] == ["mail.a.test", "mail.b.test"]
    assert entries[0].key_path == "/k/a"
    assert entries[1].cert_path == "/c/b"


def test_parse_sni_map_accepts_legacy_combined_file_format() -> None:
    text = "mail.a.test  /etc/ssl/spanel-mail/legacy-sni.pem\n"

    entries = migrate.parse_sni_map(text)

    assert [entry.hostname for entry in entries] == ["mail.a.test"]
    assert entries[0].key_path == "/etc/ssl/spanel-mail/legacy-sni.pem"
    assert entries[0].cert_path == "/etc/ssl/spanel-mail/legacy-sni.pem"


def test_upsert_allowlist_replaces_single_line_and_preserves_sections() -> None:
    text = 'allowlist = ["mail.a.test"]\n\n[verification]\nports = [993]\n'

    updated = migrate.upsert_allowlist(text, ["mail.b.test", "mail.a.test"])

    assert 'allowlist = [\n  "mail.a.test",\n  "mail.b.test",\n]' in updated
    assert "[verification]" in updated
    assert updated.count("allowlist = [") == 1


def test_remove_entries_only_drops_target_hostnames() -> None:
    text = (
        "# keep this comment\n"
        "mail.a.test /k /c\n"
        "mail.b.test /k /c\n"
        "mail.other.test /k /c\n"
    )

    updated = migrate.remove_entries(text, {"mail.a.test", "mail.b.test"})

    assert "mail.a.test" not in updated
    assert "mail.b.test" not in updated
    assert "mail.other.test" in updated
    assert "# keep this comment" in updated


def test_build_plan_detects_missing_allowlist_lineage_and_install(tmp_path: Path) -> None:
    settings = _settings(tmp_path, ["mail.a.test"])

    live_a = settings.lineage_root / "mail.a.test"
    live_a.mkdir(parents=True)
    (live_a / "fullchain.pem").write_text("cert-a", encoding="utf-8")
    installed_a = settings.install_root / "mail.a.test"
    installed_a.mkdir(parents=True)
    (installed_a / "fullchain.pem").write_text("cert-a", encoding="utf-8")
    (installed_a / "privkey.pem").write_text("key-a", encoding="utf-8")

    plan = migrate.build_plan(
        settings,
        [_entry("mail.a.test"), _entry("mail.b.test")],
    )

    by_host = {item.hostname: item for item in plan}
    assert by_host["mail.a.test"].actions == []
    assert by_host["mail.b.test"].in_allowlist is False
    assert by_host["mail.b.test"].has_lineage is False
    assert by_host["mail.b.test"].actions == ["allowlist", "issue", "install"]


def test_build_plan_reinstalls_when_installed_copy_is_stale(tmp_path: Path) -> None:
    settings = _settings(tmp_path, ["mail.a.test"])
    live = settings.lineage_root / "mail.a.test"
    live.mkdir(parents=True)
    (live / "fullchain.pem").write_text("new-cert", encoding="utf-8")
    installed = settings.install_root / "mail.a.test"
    installed.mkdir(parents=True)
    (installed / "fullchain.pem").write_text("old-cert", encoding="utf-8")
    (installed / "privkey.pem").write_text("key", encoding="utf-8")

    plan = migrate.build_plan(settings, [_entry("mail.a.test")])

    assert plan[0].actions == ["install"]


def test_select_entries_reports_hostnames_absent_from_the_map() -> None:
    try:
        migrate._select_entries([_entry("mail.a.test")], ["mail.missing.test"])
    except migrate.MigrationError as exc:
        assert "mail.missing.test" in str(exc)
    else:  # pragma: no cover - the branch must fail loudly
        raise AssertionError("missing hostname was accepted")


def test_render_acme_router_contains_all_hosts_and_marker() -> None:
    text = migrate.render_acme_router(["mail.b.test", "mail.a.test", "mail.a.test"])

    assert text.startswith(migrate.ACME_ROUTER_MARKER)
    assert f"service: {migrate.ACME_SERVICE_NAME}" in text
    assert migrate.ACME_BACKEND_URL in text
    assert "Host(`mail.a.test`)" in text
    assert "Host(`mail.b.test`)" in text
    assert migrate.parse_acme_router_hosts(text) == ["mail.a.test", "mail.b.test"]


def test_ensure_acme_routing_merges_with_existing_managed_file(tmp_path: Path) -> None:
    path = tmp_path / "spanel-acme-mail-challenge.yml"
    path.write_text(migrate.render_acme_router(["mail.a.test"]), encoding="utf-8")

    migrate.ensure_acme_routing(tmp_path, path.name, ["mail.b.test"], reload_delay=0)

    text = path.read_text(encoding="utf-8")
    assert migrate.parse_acme_router_hosts(text) == ["mail.a.test", "mail.b.test"]


def test_ensure_acme_routing_is_idempotent(tmp_path: Path) -> None:
    first, changed = migrate.ensure_acme_routing(
        tmp_path, "spanel-acme-mail-challenge.yml", ["mail.a.test"], reload_delay=0
    )
    second, changed_again = migrate.ensure_acme_routing(
        tmp_path, "spanel-acme-mail-challenge.yml", ["mail.a.test"], reload_delay=0
    )

    assert changed is True
    assert changed_again is False
    assert first == second
    assert first.read_text(encoding="utf-8").count(migrate.ACME_ROUTER_MARKER) == 1
