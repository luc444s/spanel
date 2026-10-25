"""Pruebas unitarias de `mail_cert.py` (sin Docker, sin red, sin CA).

Lo que fijan estos tests es lo que hace determinista la entrega de A.SPEC 0028:
la definición del contenedor se **deriva** de `docker inspect`, el bloque TLS se
agrega sin duplicar variables, y ante configuración desconocida el script aborta
en vez de reconstruir un contenedor distinto.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import mail_cert  # noqa: E402
from mail_cert import (  # noqa: E402
    EXIT_TRANSPORT,
    MailCertError,
    Settings,
    assert_supported,
    container_run_args,
    dedupe_chain,
    desired_env,
    renew_needed,
    split_pem_chain,
)


IMAGE_FIXTURE = {
    "Env": [
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "FETCHMAIL_POLL=300",
        "POSTGREY_AUTO_WHITELIST_CLIENTS=5",
        "POSTGREY_DELAY=300",
        "POSTGREY_MAX_AGE=35",
        "POSTGREY_TEXT=Delayed by Postgrey",
        "SASLAUTHD_MECH_OPTIONS=",
        "DMS_RELEASE=v15.1.0",
    ],
    "Labels": {"org.opencontainers.image.title": "docker-mailserver"},
    "Entrypoint": ["/usr/bin/dumb-init", "--"],
    "Cmd": ["supervisord", "-c", "/etc/supervisor/supervisord.conf"],
}


@pytest.fixture()
def fake_image(monkeypatch):
    base = copy.deepcopy(IMAGE_FIXTURE)
    monkeypatch.setattr(mail_cert, "image_config", lambda info: copy.deepcopy(base))
    return base


def inspect_fixture(**overrides) -> dict:
    """Forma real de `docker inspect spanel-mail` medido el 2026-10-25."""
    info = {
        "Id": "788d1ea7" + "0" * 56,
        "Name": "spanel-mail",
        "Image": "sha256:4f5093c251b61d5691f5ccfd3f90fcbf505585bfd045546430d282e5417658c8",
        "Config": {
            "Hostname": "mail.spanel.local",
            "User": "",
            "WorkingDir": "",
            "Image": "ghcr.io/docker-mailserver/docker-mailserver:latest",
            "Entrypoint": ["/usr/bin/dumb-init", "--"],
            "Cmd": ["supervisord", "-c", "/etc/supervisor/supervisord.conf"],
            "Env": [
                "ENABLE_FAIL2BAN=0",
                "ENABLE_POP3=1",
                "ONE_DIR=1",
                "POSTMASTER_ADDRESS=postmaster@spanel.local",
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "FETCHMAIL_POLL=300",
                "SASLAUTHD_MECH_OPTIONS=",
            ],
            "Labels": {"org.opencontainers.image.title": "docker-mailserver"},
        },
        "HostConfig": {
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
            "PortBindings": {
                "25/tcp": [{"HostIp": "", "HostPort": "25"}],
                "993/tcp": [{"HostIp": "", "HostPort": "993"}],
            },
            "LogConfig": {"Type": "json-file", "Config": {}},
            "Memory": 0,
            "NanoCpus": 0,
            "Privileged": False,
            "ReadonlyRootfs": False,
            "ShmSize": 67108864,
        },
        "Mounts": [
            {"Type": "volume", "Name": "spanel-mail-config", "Destination": "/tmp/docker-mailserver", "RW": True},
            {"Type": "volume", "Name": "spanel-mail-data", "Destination": "/var/mail", "RW": True},
            {"Type": "volume", "Name": "spanel-mail-state", "Destination": "/var/mail-state", "RW": True},
        ],
        "NetworkSettings": {"Networks": {"bridge": {}}},
    }
    info.update(overrides)
    return info


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        {
            "SPANEL_CERT_DIR": str(tmp_path / "certs"),
            "SPANEL_MAIL_DOMAIN": "mail.lucas.spanel",
            "SPANEL_MAIL_SANS": "mail.spanel.local",
            "SPANEL_CA_PASSWORD_FILE": str(tmp_path / "password"),
            "SPANEL_CA_ROOT": str(tmp_path / "root.crt"),
            "SPANEL_CA_INTERMEDIATE": str(tmp_path / "intermediate.crt"),
        }
    )


def test_settings_derivan_sans_sin_duplicados(tmp_path: Path) -> None:
    st = Settings(
        {
            "SPANEL_CERT_DIR": str(tmp_path),
            "SPANEL_MAIL_DOMAIN": "mail.lucas.spanel",
            "SPANEL_MAIL_SANS": "mail.lucas.spanel, mail.spanel.local ,,",
        }
    )
    assert st.sans == ["mail.lucas.spanel", "mail.spanel.local"]


def test_run_usa_la_imagen_por_id_y_conserva_hostname_puertos_y_volumenes(settings, fake_image) -> None:
    argv = container_run_args(inspect_fixture(), settings)
    text = " ".join(argv)
    assert "ghcr.io/docker-mailserver" not in text, "no debe usar el tag :latest"
    assert "sha256:4f5093c2" in text
    assert "--name spanel-mail" in text, "el nombre no debe llevar la barra de docker inspect"
    assert "/spanel-mail" not in text
    assert "--hostname mail.spanel.local" in text
    assert "--restart unless-stopped" in text
    assert "-p 25:25" in text and "-p 993:993" in text
    assert "spanel-mail-config:/tmp/docker-mailserver" in text
    assert "-v spanel-mail-data:/var/mail" in text
    assert "--network" not in text, "bridge por defecto no se declara"
    assert "--workdir" not in text, "no repite el workdir por defecto de la imagen"
    assert "--entrypoint" not in text, "no repite el entrypoint de la imagen"
    assert "dumb-init" not in text


def test_entrypoint_igual_al_de_la_imagen_no_se_redeclara(settings, fake_image) -> None:
    info = inspect_fixture()
    info["Config"]["Entrypoint"] = ["/bin/sh"]
    argv = container_run_args(info, settings)
    assert "--entrypoint" in argv and "/bin/sh" in argv


def test_entrypoint_con_argumentos_aborta(settings, fake_image) -> None:
    info = inspect_fixture()
    info["Config"]["Entrypoint"] = ["/usr/bin/dumb-init", "--"]
    fake_image["Entrypoint"] = ["/bin/sh"]
    with pytest.raises(MailCertError):
        container_run_args(info, settings)


def test_run_agrega_el_bloque_tls_y_el_montaje_de_solo_lectura(settings, fake_image) -> None:
    argv = container_run_args(inspect_fixture(), settings)
    assert "SSL_TYPE=manual" in argv
    assert f"SSL_CERT_PATH={settings.source_dir}/fullchain.crt" in argv
    assert f"SSL_KEY_PATH={settings.source_dir}/privkey.key" in argv
    assert f"{settings.cert_dir}:{settings.source_dir}:ro" in argv


def test_run_no_duplica_env_ni_pasa_path(settings, monkeypatch) -> None:
    monkeypatch.setattr(mail_cert, "docker", lambda *a, **k: "{}")
    env = desired_env(inspect_fixture(), settings, IMAGE_FIXTURE)
    assert not [e for e in env if e.startswith("PATH=")]
    assert len([e for e in env if e.startswith("SSL_TYPE=")]) == 1
    original = inspect_fixture()
    original["Config"]["Env"].append("SSL_TYPE=some-old-value")
    env2 = desired_env(original, settings, IMAGE_FIXTURE)
    assert [e for e in env2 if e.startswith("SSL_TYPE=")] == ["SSL_TYPE=manual"]


def test_run_reemplaza_un_montaje_previo_en_el_destino_de_certificados(settings, fake_image) -> None:
    info = inspect_fixture()
    info["Mounts"].append(
        {"Type": "bind", "Source": "/old/certs", "Destination": settings.source_dir, "RW": False}
    )
    argv = container_run_args(info, settings)
    assert "/old/certs" not in argv
    assert f"{settings.cert_dir}:{settings.source_dir}:ro" in argv


@pytest.mark.parametrize(
    "mutation",
    [
        {"Privileged": True},
        {"CapAdd": ["NET_ADMIN"]},
        {"Devices": [{"PathOnHost": "/dev/fuse"}]},
        {"Sysctls": {"net.ipv4.ip_forward": "1"}},
        {"Dns": ["1.1.1.1"]},
        {"Memory": 536870912},
        {"LogConfig": {"Type": "journald", "Config": {}}},
    ],
)
def test_aborta_ante_configuracion_que_no_sabe_reproducir(mutation, settings, fake_image) -> None:
    info = inspect_fixture()
    info["HostConfig"].update(mutation)
    with pytest.raises(MailCertError) as exc:
        container_run_args(info, settings)
    assert exc.value.code == EXIT_TRANSPORT


def test_aborta_con_mas_de_un_network(settings, fake_image) -> None:
    info = inspect_fixture()
    info["NetworkSettings"]["Networks"] = {"bridge": {}, "spanel-traefik-lab": {}}
    with pytest.raises(MailCertError):
        assert_supported(info)


def test_run_respeta_un_network_no_default(settings, fake_image) -> None:
    info = inspect_fixture()
    info["NetworkSettings"]["Networks"] = {"spanel-traefik-lab": {}}
    argv = container_run_args(info, settings)
    assert "--network spanel-traefik-lab" in " ".join(argv)


def test_dedupe_chain_elimina_repetidos() -> None:
    leaf = b"-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n"
    inter = b"-----BEGIN CERTIFICATE-----\nBBB\n-----END CERTIFICATE-----\n"
    assert dedupe_chain([leaf, inter, leaf]) == [leaf, inter]


def test_split_pem_chain_lee_la_cadena_completa() -> None:
    text = (
        "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n"
        "-----BEGIN CERTIFICATE-----\nBBB\n-----END CERTIFICATE-----\n"
    ).encode()
    assert len(split_pem_chain(text)) == 2


@pytest.mark.skipif(shutil.which("openssl") is None, reason="sin openssl")
def test_renew_needed_usa_el_porcentaje_de_vida_restante(settings, tmp_path: Path) -> None:
    cert = tmp_path / "cert.crt"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=mail.lucas.spanel", "-keyout", str(tmp_path / "k"), "-out", str(cert)],
        check=True, capture_output=True,
    )
    _, not_after = mail_cert.cert_dates(cert)
    settings.renew_at = 30
    # Recién emitido: ~100 % de vida -> no renueva.
    assert renew_needed(settings, cert) is False
    # Quedan 2 h de 24 h (~8 %) -> renueva.
    assert renew_needed(settings, cert, now=not_after - timedelta(hours=2)) is True
    # Quedan 12 h (~50 %) -> no renueva con umbral 30 %.
    assert renew_needed(settings, cert, now=not_after - timedelta(hours=12)) is False
    # Vencido -> renueva.
    assert renew_needed(settings, cert, now=not_after + timedelta(minutes=1)) is True


def test_meta_y_delivery_se_escriben_en_el_directorio_esperado(settings) -> None:
    assert settings.cert_dir.name == "mail.lucas.spanel"
    assert settings.cert_dir.parent == settings.certs_dir
    assert json.dumps(settings.sans) == '["mail.lucas.spanel", "mail.spanel.local"]'


def test_snapshot_y_restore_de_certificado(settings) -> None:
    settings.cert_dir.mkdir(parents=True)
    (settings.cert_dir / "leaf.crt").write_text("hoja-vieja")
    (settings.cert_dir / "fullchain.crt").write_text("cadena-vieja")
    (settings.cert_dir / "privkey.key").write_text("clave-vieja")
    (settings.cert_dir / "meta.json").write_text('{"serial": "VIEJO"}')

    backup = mail_cert.snapshot_cert_files(settings)
    assert backup is not None and (backup / "leaf.crt").read_text() == "hoja-vieja"

    (settings.cert_dir / "leaf.crt").write_text("hoja-nueva")
    (settings.cert_dir / "meta.json").write_text('{"serial": "NUEVO"}')
    mail_cert.restore_cert_files(settings, backup)
    assert (settings.cert_dir / "leaf.crt").read_text() == "hoja-vieja"
    assert (settings.cert_dir / "meta.json").read_text() == '{"serial": "VIEJO"}'


def test_sin_certificado_no_hay_snapshot(settings) -> None:
    settings.cert_dir.mkdir(parents=True)
    assert mail_cert.snapshot_cert_files(settings) is None


def test_delivery_matches_exige_el_serial_que_presenta_el_puerto(settings, monkeypatch) -> None:
    info = inspect_fixture()
    info["Config"]["Env"] += [
        "SSL_TYPE=manual",
        f"SSL_CERT_PATH={settings.source_dir}/fullchain.crt",
        f"SSL_KEY_PATH={settings.source_dir}/privkey.key",
    ]
    info["Mounts"].append(
        {"Type": "bind", "Source": str(settings.cert_dir), "Destination": settings.source_dir, "RW": False}
    )
    meta = {"serial": "AA11"}

    monkeypatch.setattr(mail_cert, "_presented_serial", lambda st: "AA11")
    assert mail_cert.delivery_matches(settings, info, meta) is True

    # Misma configuración, pero el proceso sigue sirviendo el certificado viejo.
    monkeypatch.setattr(mail_cert, "_presented_serial", lambda st: "BB22")
    assert mail_cert.delivery_matches(settings, info, meta) is False


def test_delivery_matches_falla_si_no_responden_los_puertos(settings, monkeypatch) -> None:
    info = inspect_fixture()
    info["Config"]["Env"] += [
        "SSL_TYPE=manual",
        f"SSL_CERT_PATH={settings.source_dir}/fullchain.crt",
        f"SSL_KEY_PATH={settings.source_dir}/privkey.key",
    ]
    info["Mounts"].append(
        {"Type": "bind", "Source": str(settings.cert_dir), "Destination": settings.source_dir, "RW": False}
    )

    def boom(st):
        raise mail_cert.MailCertError("sin TLS", 8)

    monkeypatch.setattr(mail_cert, "_presented_serial", boom)
    assert mail_cert.delivery_matches(settings, info, {"serial": "AA11"}) is False
