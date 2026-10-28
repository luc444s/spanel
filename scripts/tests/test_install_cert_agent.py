"""Instalador del agente de certificados: helpers sin tocar root ni systemd."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import install_cert_agent


def _lineage(root: Path, hostname: str, *, complete: bool = True) -> Path:
    directory = root / hostname
    directory.mkdir(parents=True)
    (directory / "fullchain.pem").write_text("cert", encoding="utf-8")
    if complete:
        (directory / "privkey.pem").write_text("key", encoding="utf-8")
    return directory


def test_normaliza_y_rechaza_hostnames() -> None:
    assert install_cert_agent._normalize_hostname(" Mail.Systutor.COM. ") == "mail.systutor.com"
    with pytest.raises(install_cert_agent.InstallError):
        install_cert_agent._normalize_hostname("https://x")


def test_descubre_los_lineajes_completos(tmp_path: Path) -> None:
    _lineage(tmp_path, "mail.systutor.com")
    _lineage(tmp_path, "mail.incompleto.pe", complete=False)
    _lineage(tmp_path, "algisac.com")

    assert install_cert_agent._discover_allowlist(tmp_path) == [
        "algisac.com",
        "mail.systutor.com",
    ]


def test_config_no_habilita_emision_por_defecto(tmp_path: Path) -> None:
    text = install_cert_agent._render_config(
        allowlist=["mail.systutor.com"],
        install_root=tmp_path / "mail",
        state_dir=tmp_path / "state",
        default_cert=None,
        default_key=None,
        certbot="/usr/bin/certbot",
        webroot=tmp_path / "webroot",
        ca_bundle=tmp_path / "ca.pem",
        lineages=tmp_path / "live",
    )

    assert 'allowlist = ["mail.systutor.com"]' in text
    assert 'default_certificate = ""' in text
    assert f'webroot_path = "{tmp_path / "webroot"}"' in text
    assert "\n[postfix]" not in text
    assert "\n[dovecot]" not in text


def test_detecta_webroot_desde_renewal(tmp_path: Path) -> None:
    renewal = tmp_path / "renewal"
    renewal.mkdir()
    (renewal / "mail.example.com.conf").write_text(
        "[renewalparams]\nauthenticator = webroot\nwebroot_path = /srv/acme,\n",
        encoding="utf-8",
    )

    assert install_cert_agent._detect_webroot(renewal) == Path("/srv/acme")


def test_unidades_usan_las_rutas_reales(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    units = install_cert_agent._render_units(
        repo_root=repo,
        python="/usr/bin/python3",
        config=tmp_path / "cert-agent.toml",
        state_dir=tmp_path / "state",
        docs=repo / "docs" / "certificate-manager.md",
    )

    agent = units["spanel-cert-agent.service"]
    assert f"WorkingDirectory={repo}" in agent
    assert f"PYTHONPATH={repo / 'scripts'}" in agent
    assert f"--config {tmp_path / 'cert-agent.toml'}" in agent
    assert "/opt/spanel" not in agent


def test_config_respeta_archivo_existente(tmp_path: Path) -> None:
    path = tmp_path / "cert-agent.toml"
    path.write_text("original", encoding="utf-8")

    assert install_cert_agent._write_config(path, "nuevo", force=False, dry_run=False) == "conservada"
    assert path.read_text(encoding="utf-8") == "original"

    assert install_cert_agent._write_config(path, "nuevo", force=True, dry_run=False) == "escrita"
    assert path.read_text(encoding="utf-8") == "nuevo"


def test_siembra_solo_cuando_falta_y_respeta_refresh(tmp_path: Path) -> None:
    lineages = tmp_path / "live"
    _lineage(lineages, "mail.systutor.com")
    install_root = tmp_path / "install"

    seeded, skipped = install_cert_agent._seed_certificates(
        ["mail.systutor.com", "mail.sinlinaje.com"],
        lineages=lineages,
        install_root=install_root,
        refresh=False,
        dry_run=False,
    )
    assert seeded == ["mail.systutor.com"]
    assert skipped == ["mail.sinlinaje.com"]
    assert (install_root / "mail.systutor.com" / "fullchain.pem").read_text() == "cert"

    (install_root / "mail.systutor.com" / "fullchain.pem").write_text("otro")
    seeded, _ = install_cert_agent._seed_certificates(
        ["mail.systutor.com"],
        lineages=lineages,
        install_root=install_root,
        refresh=False,
        dry_run=False,
    )
    assert seeded == []
    assert (install_root / "mail.systutor.com" / "fullchain.pem").read_text() == "otro"

    seeded, _ = install_cert_agent._seed_certificates(
        ["mail.systutor.com"],
        lineages=lineages,
        install_root=install_root,
        refresh=True,
        dry_run=False,
    )
    assert seeded == ["mail.systutor.com"]
    assert (install_root / "mail.systutor.com" / "fullchain.pem").read_text() == "cert"


def test_dry_run_no_escribe(tmp_path: Path) -> None:
    lineages = tmp_path / "live"
    _lineage(lineages, "mail.systutor.com")
    install_root = tmp_path / "install"

    seeded, _ = install_cert_agent._seed_certificates(
        ["mail.systutor.com"],
        lineages=lineages,
        install_root=install_root,
        refresh=False,
        dry_run=True,
    )

    assert seeded == ["mail.systutor.com"]
    assert not install_root.exists()


def test_edad_del_estado(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text('{"generated_at": "2020-01-01T00:00:00Z"}', encoding="utf-8")

    age = install_cert_agent._state_age_seconds(state)

    assert age is not None and age > 0
    state.write_text("no-json", encoding="utf-8")
    assert install_cert_agent._state_age_seconds(state) is None
