#!/usr/bin/env python3
"""Certificado TLS para docker-mailserver con la CA interna (A.SPEC 0028).

Hace determinista lo que se ejecutó a mano el 2026-10-25:

  1. emite el certificado contra el provisioner JWK de step-ca,
  2. lo entrega a docker-mailserver recreando el contenedor **a partir de su
     propia definición** (`docker inspect`), no de flags escritos a mano,
  3. verifica TLS en 993/465/587 y que las cuentas, el vhost y el maildir no
     perdieron nada,
  4. si algo falla, restaura el contenedor anterior.

Nunca inventa configuración: si el contenedor tiene algo que este script no sabe
reproducir (capabilities, devices, sysctls, DNS propio, varios networks…), aborta
en vez de reconstruirlo perdiendo ese detalle.

Comandos:
  python3 scripts/mail_cert.py status
  python3 scripts/mail_cert.py check
  python3 scripts/mail_cert.py issue --dry-run
  python3 scripts/mail_cert.py issue --deliver mail --yes
  python3 scripts/mail_cert.py renew --all --yes

Códigos de salida: 0 ok, 2 uso/validación, 4 Docker, 5 CA/emisión,
7 entrega, 8 verificación TLS posterior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
LAB_DIR = SCRIPTS_DIR / "traefik-lab"

EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_TRANSPORT = 4
EXIT_CA = 5
EXIT_DELIVERY = 7
EXIT_VERIFY = 8

DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
PEM_CERT_RE = re.compile(
    rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----\r?\n?", re.DOTALL
)
# Configuración de contenedor que este script no sabe reconstruir.
UNSUPPORTED = (
    ("Privileged", False),
    ("ReadonlyRootfs", False),
    ("CapAdd", None),
    ("CapDrop", None),
    ("Devices", []),
    ("Sysctls", {}),
    ("Tmpfs", {}),
    ("Ulimits", []),
    ("Dns", []),
    ("DnsOptions", []),
    ("DnsSearch", []),
    ("ExtraHosts", []),
    ("PidMode", ""),
    ("UTSMode", ""),
    ("CgroupParent", ""),
    ("Memory", 0),
    ("NanoCpus", 0),
)


class MailCertError(Exception):
    def __init__(self, message: str, code: int = EXIT_VALIDATION) -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- #
# Configuración efectiva
# --------------------------------------------------------------------------- #


class Settings:
    """Configuración por entorno; nunca por flags de línea de comandos."""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        env = os.environ if env is None else env

        def get(*names: str, default: str = "") -> str:
            for name in names:
                value = env.get(name)
                if value:
                    return value
            return default

        self.container = get("SPANEL_MAIL_CONTAINER", "MAIL_DMS_CONTAINER", default="spanel-mail")
        self.domain = get("SPANEL_MAIL_DOMAIN", default="mail.lucas.spanel")
        self.extra_sans = [
            item.strip() for item in get("SPANEL_MAIL_SANS", default="mail.spanel.local").split(",")
            if item.strip()
        ]
        self.source_dir = get("SPANEL_MAIL_SOURCE_DIR", default="/etc/dms/source")
        self.certs_dir = Path(get("SPANEL_CERT_DIR", default=str(LAB_DIR / "certs"))).expanduser()
        self.ca_container = get("SPANEL_CA_CONTAINER", default="spanel-step-ca")
        self.ca_volume = get("SPANEL_CA_VOLUME", default="spanel-step-ca-data")
        self.ca_url = get("SPANEL_CA_URL", default="https://spanel-step-ca:9000")
        self.ca_password_file = Path(
            get("SPANEL_CA_PASSWORD_FILE", default=str(LAB_DIR / "ca" / "password"))
        ).expanduser()
        self.ca_root = Path(
            get("SPANEL_CA_ROOT", default=str(LAB_DIR / "ca" / "root_ca.crt"))
        ).expanduser()
        self.ca_intermediate = Path(
            get("SPANEL_CA_INTERMEDIATE", default=str(LAB_DIR / "ca" / "intermediate_ca.crt"))
        ).expanduser()
        self.network = get("SPANEL_LAB_NETWORK", default="spanel-traefik-lab")
        self.step_image = get("SPANEL_STEP_IMAGE", default="smallstep/step-ca:0.30.2")
        self.provisioner = get("SPANEL_CA_PROVISIONER", default="admin")
        self.not_after = get("SPANEL_CERT_NOT_AFTER", default="720h")
        self.renew_at = int(get("SPANEL_CERT_RENEW_AT", default="30"))
        self.ports = [
            int(item) for item in get("SPANEL_MAIL_PORTS", default="993,465,587").split(",")
            if item.strip()
        ]
        self.timeout = int(get("SPANEL_MAIL_TIMEOUT", default="120"))
        self.host = get("SPANEL_MAIL_HOST", default="127.0.0.1")

    @property
    def cert_dir(self) -> Path:
        return self.certs_dir / self.domain

    @property
    def sans(self) -> list[str]:
        out: list[str] = []
        for name in [self.domain, *self.extra_sans]:
            if name not in out:
                out.append(name)
        return out


# --------------------------------------------------------------------------- #
# Procesos y Docker
# --------------------------------------------------------------------------- #


def docker(*args: str, check: bool = True) -> str:
    proc = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise MailCertError(f"docker {args[0]} falló: {detail}", EXIT_TRANSPORT)
    return proc.stdout.strip()


def inspect_container(name: str) -> dict:
    proc = subprocess.run(["docker", "inspect", name], capture_output=True, text=True)
    if proc.returncode != 0:
        raise MailCertError(f"no existe el contenedor '{name}'", EXIT_TRANSPORT)
    data = json.loads(proc.stdout)
    if not data:
        raise MailCertError(f"docker inspect vacío para '{name}'", EXIT_TRANSPORT)
    return data[0]


def image_config(info: dict) -> dict:
    raw = docker("image", "inspect", info["Image"], "--format", "{{json .Config}}")
    try:
        return json.loads(raw) or {}
    except json.JSONDecodeError as exc:
        raise MailCertError(f"no se pudo leer la imagen {info['Image']}", EXIT_TRANSPORT) from exc


def assert_supported(info: dict) -> None:
    """Aborta si el contenedor tiene configuración que no sabemos reproducir."""
    host = info["HostConfig"]
    problems: list[str] = []
    for key, expected in UNSUPPORTED:
        value = host.get(key)
        if expected is None:
            if value:
                problems.append(f"{key}={value}")
            continue
        if value is not None and value != expected:
            problems.append(f"{key}={value}")
    if host.get("RestartPolicy", {}).get("MaximumRetryCount"):
        problems.append("RestartPolicy.MaximumRetryCount")
    log_type = host.get("LogConfig", {}).get("Type")
    if log_type not in (None, "json-file"):
        problems.append(f"LogConfig={log_type}")
    networks = list(info.get("NetworkSettings", {}).get("Networks", {}))
    if len(networks) > 1:
        problems.append(f"networks={networks}")
    if problems:
        raise MailCertError(
            "el contenedor tiene configuración que este script no reproduce: "
            + ", ".join(problems),
            EXIT_TRANSPORT,
        )


def custom_env(info: dict, image: dict) -> list[str]:
    """Solo las variables que no son el valor por defecto de la imagen."""
    defaults = {}
    for item in image.get("Env") or []:
        if "=" in item:
            key, value = item.split("=", 1)
            defaults[key] = value
    out = []
    for item in info["Config"].get("Env") or []:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        if defaults.get(key) == value:
            continue
        out.append(item)
    return out


def custom_labels(info: dict, image: dict) -> dict[str, str]:
    labels = info["Config"].get("Labels") or {}
    defaults = image.get("Labels") or {}
    return {key: value for key, value in labels.items() if defaults.get(key) != value}


def desired_env(info: dict, settings: Settings, image: dict) -> list[str]:
    tls = {
        "SSL_TYPE": "manual",
        "SSL_CERT_PATH": f"{settings.source_dir}/fullchain.crt",
        "SSL_KEY_PATH": f"{settings.source_dir}/privkey.key",
    }
    kept: list[str] = []
    for item in custom_env(info, image):
        key = item.split("=", 1)[0]
        if key not in tls:
            kept.append(item)
    for key, value in tls.items():
        kept.append(f"{key}={value}")
    return kept


def port_flags(info: dict) -> list[str]:
    flags: list[str] = []
    bindings = info["HostConfig"].get("PortBindings") or {}
    for container_port, hosts in sorted(bindings.items()):
        for binding in hosts or []:
            ip = binding.get("HostIp") or ""
            host_port = binding.get("HostPort") or container_port.split("/")[0]
            spec = f"{host_port}:{container_port}"
            if ip:
                spec = f"{ip}:{spec}"
            flags += ["-p", spec]
    return flags


def mount_flags(info: dict, *, replace_destination: str | None = None) -> list[str]:
    flags: list[str] = []
    for mount in info.get("Mounts") or []:
        if replace_destination and mount.get("Destination") == replace_destination:
            continue
        if mount.get("Type") == "volume":
            source = mount.get("Name")
        else:
            source = mount.get("Source")
        if not source:
            raise MailCertError(
                f"montaje sin origen en {mount.get('Destination')}", EXIT_TRANSPORT
            )
        spec = f"{source}:{mount['Destination']}"
        if not mount.get("RW", True):
            spec += ":ro"
        flags += ["-v", spec]
    return flags


def container_run_args(
    info: dict, settings: Settings, *, executable: str = "docker"
) -> list[str]:
    """Construye `docker run -d` de forma determinista desde `docker inspect`."""
    assert_supported(info)
    image = image_config(info)
    config = info["Config"]
    host = info["HostConfig"]

    argv = [executable, "run", "-d", "--name", info["Name"].lstrip("/")]
    if config.get("Hostname") and config["Hostname"] != image.get("Hostname"):
        argv += ["--hostname", config["Hostname"]]
    if config.get("User"):
        argv += ["--user", config["User"]]
    if config.get("WorkingDir") and config["WorkingDir"] != image.get("WorkingDir"):
        argv += ["--workdir", config["WorkingDir"]]

    restart = host.get("RestartPolicy", {}).get("Name") or ""
    if restart and restart != "no":
        argv += ["--restart", restart]

    networks = list(info.get("NetworkSettings", {}).get("Networks", {}))
    if networks and networks[0] not in ("bridge", "default"):
        argv += ["--network", networks[0]]

    for key, value in sorted(custom_labels(info, image).items()):
        argv += ["--label", f"{key}={value}"]

    entrypoint = config.get("Entrypoint") or []
    if entrypoint and entrypoint != (image.get("Entrypoint") or []):
        if len(entrypoint) != 1:
            raise MailCertError(
                "entrypoint con argumentos que no se puede expresar con --entrypoint; "
                "abortando para no cambiar el arranque",
                EXIT_TRANSPORT,
            )
        argv += ["--entrypoint", entrypoint[0]]
    argv += port_flags(info)
    argv += mount_flags(info, replace_destination=settings.source_dir)
    argv += ["-v", f"{settings.cert_dir}:{settings.source_dir}:ro"]
    for item in desired_env(info, settings, image):
        argv += ["-e", item]
    argv.append(info["Image"])
    command = list(config.get("Cmd") or [])
    if command and command != (image.get("Cmd") or []):
        argv += command
    return argv


# --------------------------------------------------------------------------- #
# Certificado
# --------------------------------------------------------------------------- #


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def split_pem_chain(data: bytes) -> list[bytes]:
    return [match.group(0) for match in PEM_CERT_RE.finditer(data)]


def dedupe_chain(blocks: list[bytes]) -> list[bytes]:
    seen: set[str] = set()
    out: list[bytes] = []
    for block in blocks:
        key = block.replace(b"\r\n", b"\n").strip().decode("latin-1")
        if key in seen:
            continue
        seen.add(key)
        out.append(block.rstrip() + b"\n")
    return out


def openssl(*args: str) -> str:
    proc = subprocess.run(["openssl", *args], capture_output=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        raise MailCertError(f"openssl {args[0]} falló: {detail}", EXIT_CA)
    return proc.stdout.decode("utf-8", "replace")


def cert_serial(path: Path) -> str:
    out = openssl("x509", "-in", str(path), "-noout", "-serial").strip()
    return out.split("=", 1)[1] if "=" in out else out


def cert_dates(path: Path) -> tuple[datetime, datetime]:
    not_before = openssl("x509", "-in", str(path), "-noout", "-startdate").split("=", 1)[1].strip()
    not_after = openssl("x509", "-in", str(path), "-noout", "-enddate").split("=", 1)[1].strip()
    fmt = "%b %d %H:%M:%S %Y %Z"
    return (
        datetime.strptime(not_before, fmt).replace(tzinfo=timezone.utc),
        datetime.strptime(not_after, fmt).replace(tzinfo=timezone.utc),
    )


def cert_sans(path: Path) -> list[str]:
    text = openssl("x509", "-in", str(path), "-noout", "-ext", "subjectAltName")
    return sorted(
        part.strip().replace("DNS:", "")
        for line in text.splitlines()[1:]
        for part in line.split(",")
        if "DNS:" in part
    )


def verify_chain(settings: Settings, leaf: Path) -> None:
    for needed in (settings.ca_root, settings.ca_intermediate):
        if not needed.exists():
            raise MailCertError(f"falta {needed}", EXIT_CA)
    proc = subprocess.run(
        [
            "openssl", "verify",
            "-CAfile", str(settings.ca_root),
            "-untrusted", str(settings.ca_intermediate),
            str(leaf),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise MailCertError(f"la cadena no valida: {proc.stdout.strip()}", EXIT_CA)


def renew_needed(settings: Settings, leaf: Path, now: datetime | None = None) -> bool:
    """Renueva solo por debajo del umbral de vida restante (30 % por defecto)."""
    try:
        not_before, not_after = cert_dates(leaf)
    except (MailCertError, IndexError, ValueError):
        return True
    reference = now or now_utc()
    total = (not_after - not_before).total_seconds()
    left = (not_after - reference).total_seconds()
    if total <= 0 or left <= 0:
        return True
    return (left / total) * 100 < settings.renew_at


def emit(settings: Settings) -> None:
    """Emite con el contenedor efímero escribiendo en un directorio temporal."""
    for needed in (settings.ca_password_file, settings.ca_root):
        if not needed.exists():
            raise MailCertError(f"falta {needed}", EXIT_CA)
    if not settings.ca_intermediate.exists():
        docker(
            "cp",
            f"{settings.ca_container}:/home/step/certs/intermediate_ca.crt",
            str(settings.ca_intermediate),
        )
        os.chmod(settings.ca_intermediate, 0o644)

    work = settings.cert_dir / ".issue"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    argv = [
        "docker", "run", "--rm",
        "--label", "com.spanel.lab=true",
        "--label", "com.spanel.ephemeral=true",
        "--network", settings.network,
        "-v", f"{settings.ca_volume}:/ca:ro",
        "-v", f"{settings.ca_password_file}:/secrets/password:ro",
        "-v", f"{work}:/out",
        settings.step_image,
        "step", "ca", "certificate", settings.domain,
        "/out/leaf.crt", "/out/privkey.key",
        "--ca-url", settings.ca_url,
        "--root", "/ca/certs/root_ca.crt",
        "--provisioner", settings.provisioner,
        "--provisioner-password-file", "/secrets/password",
        "--kty", "RSA", "--size", "2048",
        "--not-after", settings.not_after,
    ]
    for san in settings.sans:
        argv += ["--san", san]

    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout).strip().splitlines()
        raise MailCertError(
            "la CA rechazó la emisión: " + (lines[-1] if lines else "sin detalle"), EXIT_CA
        )

    leaf, key, chain = settings.cert_dir / "leaf.crt", settings.cert_dir / "privkey.key", (
        settings.cert_dir / "fullchain.crt"
    )
    shutil.copyfile(work / "leaf.crt", leaf)
    shutil.copyfile(work / "privkey.key", key)
    shutil.rmtree(work, ignore_errors=True)
    os.chmod(leaf, 0o644)
    os.chmod(key, 0o600)

    # `step ca certificate` ya devuelve hoja + intermedio; si no, se agrega.
    blocks = split_pem_chain(leaf.read_bytes())
    if len(blocks) < 2:
        blocks += split_pem_chain(settings.ca_intermediate.read_bytes())
    chain.write_bytes(b"".join(dedupe_chain(blocks)))
    os.chmod(chain, 0o644)

    verify_chain(settings, leaf)
    not_before, not_after = cert_dates(leaf)
    meta = {
        "subject": openssl("x509", "-in", str(leaf), "-noout", "-subject").split("=", 1)[1].strip(),
        "sans": cert_sans(leaf),
        "issuer": openssl("x509", "-in", str(leaf), "-noout", "-issuer").split("=", 1)[1].strip(),
        "serial": cert_serial(leaf),
        "not_before": not_before.isoformat(),
        "not_after": not_after.isoformat(),
        "sha256_fullchain": hashlib.sha256(chain.read_bytes()).hexdigest(),
        "sha256_privkey": hashlib.sha256(key.read_bytes()).hexdigest(),
        "provisioner": f"{settings.provisioner} (JWK)",
        "profile": "internal",
        "issued_at": now_utc().isoformat(timespec="seconds"),
    }
    (settings.cert_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")


# --------------------------------------------------------------------------- #
# Verificación TLS
# --------------------------------------------------------------------------- #


def tls_check(settings: Settings, port: int) -> None:
    args = [
        "s_client", "-connect", f"{settings.host}:{port}",
        "-servername", settings.domain,
        "-CAfile", str(settings.ca_root),
    ]
    if port == 587:
        args = ["s_client", "-starttls", "smtp"] + args[1:]
    proc = subprocess.run(["openssl", *args], input=b"", capture_output=True)
    text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    if "Verify return code: 0 (ok)" not in text:
        reason = next(
            (line.strip() for line in text.splitlines() if "Verify return code" in line),
            "sin handshake",
        )
        raise MailCertError(f"puerto {port}: {reason}", EXIT_VERIFY)


def wait_tls(settings: Settings, port: int = 993, timeout: int | None = None) -> None:
    deadline = time.time() + (timeout or settings.timeout)
    last: Exception | None = None
    while time.time() < deadline:
        try:
            tls_check(settings, port)
            return
        except MailCertError as exc:
            last = exc
            time.sleep(3)
    raise MailCertError(
        f"TLS no respondió en {timeout or settings.timeout}s ({last})", EXIT_VERIFY
    )


def snapshot(settings: Settings) -> dict:
    def shell(command: str) -> str:
        return docker("exec", settings.container, "sh", "-c", command)

    accounts = sorted(
        line.strip().lstrip("* ").split(" ")[0]
        for line in shell("setup email list 2>/dev/null").splitlines()
        if "@" in line
    )
    vhost = sorted(filter(None, shell("cat /etc/postfix/vhost").splitlines()))
    maildir = sorted(filter(None, shell('find /var/mail -type f -printf "%p %s\\n"').splitlines()))
    return {"accounts": accounts, "vhost": vhost, "maildir": maildir}


def lost_files(before: list[str], after: list[str]) -> list[str]:
    """Archivos del maildir que estaban antes y ya no están."""
    return sorted(set(before) - set(after))


# --------------------------------------------------------------------------- #
# Entrega
# --------------------------------------------------------------------------- #


def delivered_state(settings: Settings, info: dict) -> dict:
    env = {}
    for item in info["Config"].get("Env") or []:
        if "=" in item:
            key, value = item.split("=", 1)
            env[key] = value
    mount = next(
        (m for m in info.get("Mounts") or [] if m.get("Destination") == settings.source_dir),
        None,
    )
    return {
        "ssl_type": env.get("SSL_TYPE"),
        "cert_path": env.get("SSL_CERT_PATH"),
        "key_path": env.get("SSL_KEY_PATH"),
        "mount": (mount or {}).get("Source"),
        "mount_ro": not (mount or {}).get("RW", True) if mount else False,
    }


def delivery_matches(settings: Settings, info: dict) -> bool:
    state = delivered_state(settings, info)
    return (
        state["ssl_type"] == "manual"
        and state["cert_path"] == f"{settings.source_dir}/fullchain.crt"
        and state["key_path"] == f"{settings.source_dir}/privkey.key"
        and state["mount"] == str(settings.cert_dir)
        and state["mount_ro"] is True
    )


def deliver(settings: Settings, *, dry_run: bool = False, assume_yes: bool = False) -> dict:
    info = inspect_container(settings.container)
    assert_supported(info)
    meta_path = settings.cert_dir / "meta.json"
    if not meta_path.exists():
        raise MailCertError("no hay certificado emitido (falta meta.json)", EXIT_DELIVERY)
    meta = json.loads(meta_path.read_text())
    current = hashlib.sha256((settings.cert_dir / "fullchain.crt").read_bytes()).hexdigest()
    if meta.get("sha256_fullchain") != current:
        raise MailCertError("meta.json no corresponde a fullchain.crt; reemitir", EXIT_DELIVERY)

    argv = container_run_args(info, settings)
    if dry_run:
        print("\n".join(_shell_lines(argv)))
        return {"dry_run": True}
    if delivery_matches(settings, info):
        return {"changed": False, "reason": "el contenedor ya usa este certificado"}
    if not assume_yes and not sys.stdin.isatty():
        raise MailCertError("la entrega recrea el contenedor; repetir con --yes", EXIT_VALIDATION)

    stamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    backups = settings.cert_dir / "backups" / stamp
    backups.mkdir(parents=True, exist_ok=True)
    os.chmod(settings.cert_dir / "backups", 0o700)
    (backups / "container.json").write_text(json.dumps(info, indent=2) + "\n")
    before = snapshot(settings)
    (backups / "before.json").write_text(json.dumps(before, indent=2) + "\n")
    previous_record = settings.cert_dir / "deliveries" / "mail.json"
    if previous_record.exists():
        shutil.copy2(previous_record, backups / "mail.json")

    previous_container = f"{settings.container}.pre-mailtls-{stamp}"
    docker("stop", settings.container)
    docker("rename", settings.container, previous_container)

    def rollback(reason: str) -> MailCertError:
        subprocess.run(["docker", "rm", "-f", settings.container], capture_output=True)
        subprocess.run(["docker", "rename", previous_container, settings.container], capture_output=True)
        subprocess.run(["docker", "start", settings.container], capture_output=True)
        try:
            wait_tls(settings, 993, 60)
        except MailCertError:
            return MailCertError(
                f"{reason}. Rollback ejecutado pero el correo no responde; revisar a mano",
                EXIT_VERIFY,
            )
        return MailCertError(f"{reason}. Se restauró el contenedor anterior", EXIT_DELIVERY)

    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        raise rollback(f"docker run falló: {(proc.stderr or proc.stdout).strip()}")

    try:
        wait_tls(settings, 993)
        for port in settings.ports:
            tls_check(settings, port)
        after = snapshot(settings)
        if after["vhost"] != before["vhost"]:
            raise MailCertError("cambió /etc/postfix/vhost", EXIT_VERIFY)
        missing = lost_files(before["maildir"], after["maildir"])
        if missing:
            raise MailCertError(f"se perdieron {len(missing)} archivos del maildir", EXIT_VERIFY)
        if not set(before["accounts"]) <= set(after["accounts"]):
            raise MailCertError("se perdieron cuentas", EXIT_VERIFY)
        if _presented_serial(settings) != meta["serial"]:
            raise MailCertError("el contenedor no presenta el certificado nuevo", EXIT_VERIFY)
    except MailCertError as exc:
        raise rollback(str(exc)) from exc

    docker("rm", "-f", previous_container)
    deliveries = settings.cert_dir / "deliveries"
    deliveries.mkdir(exist_ok=True)
    record = {
        "target": "mail",
        "container": settings.container,
        "container_id": docker("inspect", "--format", "{{.Id}}", settings.container),
        "image": info["Image"],
        "ssl_type": "manual",
        "cert_path": f"{settings.source_dir}/fullchain.crt",
        "key_path": f"{settings.source_dir}/privkey.key",
        "host_mount": f"{settings.cert_dir} -> {settings.source_dir} (ro)",
        "cert_sha256": meta["sha256_fullchain"],
        "serial": meta["serial"],
        "verified": {f"port_{port}": True for port in settings.ports},
        "delivered_at": now_utc().isoformat(timespec="seconds"),
    }
    (deliveries / "mail.json").write_text(json.dumps(record, indent=2) + "\n")
    return {"changed": True, **record}


def _presented_serial(settings: Settings) -> str:
    proc = subprocess.run(
        ["openssl", "s_client", "-connect", f"{settings.host}:993", "-servername", settings.domain],
        input=b"", capture_output=True,
    )
    with tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False) as tmp:
        tmp.write(proc.stdout)
        path = Path(tmp.name)
    try:
        return cert_serial(path)
    finally:
        path.unlink(missing_ok=True)


def _shell_lines(argv: list[str]) -> list[str]:
    lines: list[str] = []
    line = ""
    for token in argv:
        piece = token if re.fullmatch(r"[A-Za-z0-9_./:=@,*+-]+", token) else json.dumps(token)
        if len(line) + len(piece) + 1 > 100:
            lines.append(line)
            line = "  " + piece
        else:
            line = f"{line} {piece}" if line else piece
    if line:
        lines.append(line)
    return lines


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #


def cmd_status(settings: Settings) -> int:
    meta_path = settings.cert_dir / "meta.json"
    if not meta_path.exists():
        print(f"{settings.domain}: sin certificado en {settings.cert_dir}")
        return EXIT_OK
    meta = json.loads(meta_path.read_text())
    leaf = settings.cert_dir / "leaf.crt"
    not_before, not_after = cert_dates(leaf)
    left = (not_after - now_utc()).total_seconds() / 86400
    total = (not_after - not_before).total_seconds()
    pct = (left * 86400 / total * 100) if total else 0
    state = "expired" if left <= 0 else ("expiring" if pct < settings.renew_at else "active")
    print(settings.domain)
    print(f"  subject   {meta['subject']}")
    print(f"  sans      {', '.join(meta['sans'])}")
    print(f"  issuer    {meta['issuer']}")
    print(f"  serial    {meta['serial']}")
    print(f"  not_after {not_after.isoformat(timespec='seconds')} ({left:.1f} días, {pct:.0f}%)")
    print(f"  sha256    {meta['sha256_fullchain'][:16]}…")
    print(f"  state     {state}")
    record = settings.cert_dir / "deliveries" / "mail.json"
    if record.exists():
        data = json.loads(record.read_text())
        print(f"  entrega   {data['container']} ({data['container_id'][:12]}) "
              f"el {data['delivered_at']}")
    else:
        print("  entrega   not_delivered")
    return EXIT_OK


def cmd_check(settings: Settings) -> int:
    leaf = settings.cert_dir / "leaf.crt"
    if not leaf.exists():
        print(f"FALLA: no hay certificado en {settings.cert_dir}")
        return EXIT_VERIFY
    problems: list[str] = []
    try:
        verify_chain(settings, leaf)
        print("ok   cadena válida contra la CA local")
    except MailCertError as exc:
        problems.append(str(exc))
    meta_path = settings.cert_dir / "meta.json"
    if not meta_path.exists():
        problems.append("falta meta.json")
    else:
        meta = json.loads(meta_path.read_text())
        if cert_sans(leaf) != sorted(meta["sans"]):
            problems.append("los SAN del archivo no coinciden con meta.json")
        else:
            print(f"ok   SAN {', '.join(meta['sans'])}")
        if renew_needed(settings, leaf):
            print("aviso certificado por debajo del umbral de renovación")
    try:
        info = inspect_container(settings.container)
        if delivery_matches(settings, info):
            print(f"ok   {settings.container} usa este certificado (montaje :ro)")
        else:
            problems.append(
                f"{settings.container} no usa este certificado: "
                f"{json.dumps(delivered_state(settings, info), ensure_ascii=False)}"
            )
    except MailCertError as exc:
        problems.append(str(exc))
    for port in settings.ports:
        try:
            tls_check(settings, port)
            print(f"ok   TLS {port} valida con la CA local")
        except MailCertError as exc:
            problems.append(str(exc))
    if problems:
        print("\nFALLAS:")
        for item in problems:
            print(f"  - {item}")
        return EXIT_VERIFY
    print("\nresultado: OK")
    return EXIT_OK


def cmd_issue(settings: Settings, args: argparse.Namespace) -> int:
    leaf = settings.cert_dir / "leaf.crt"
    fresh = bool(
        leaf.exists()
        and not renew_needed(settings, leaf)
        and cert_sans(leaf) == settings.sans
        and (settings.cert_dir / "meta.json").exists()
    )
    if fresh and not args.force:
        print(f"{settings.domain}: sin cambios (serial {cert_serial(leaf)})")
    elif args.dry_run:
        print(
            f"emitiría {settings.domain} --not-after {settings.not_after} "
            + " ".join(f"--san {san}" for san in settings.sans)
        )
    else:
        settings.cert_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(settings.cert_dir, 0o700)
        emit(settings)
        meta = json.loads((settings.cert_dir / "meta.json").read_text())
        print(f"emitido {settings.domain} serial {meta['serial']} hasta {meta['not_after']}")

    if args.deliver != "mail":
        return EXIT_OK
    if args.dry_run:
        print("\nrecrearía el contenedor con:")
        print("\n".join(_shell_lines(container_run_args(inspect_container(settings.container), settings))))
        return EXIT_OK
    result = deliver(settings, assume_yes=args.yes)
    if result.get("changed"):
        print(f"entregado a {settings.container} ({result['container_id'][:12]})")
    else:
        print(f"entrega sin cambios: {result.get('reason')}")
    return EXIT_OK


def managed_cert_dirs(settings: Settings) -> list[Path]:
    if not settings.certs_dir.exists():
        return []
    return sorted(
        path for path in settings.certs_dir.iterdir()
        if path.is_dir() and (path / "meta.json").exists()
    )


def cmd_renew(settings: Settings, args: argparse.Namespace) -> int:
    targets = managed_cert_dirs(settings) if args.all else [settings.cert_dir]
    if not targets:
        print("no hay certificados gestionados")
        return EXIT_OK
    for directory in targets:
        leaf = directory / "leaf.crt"
        if not leaf.exists():
            print(f"{directory.name}: sin leaf.crt, se omite")
            continue
        if renew_needed(settings, leaf) or args.force:
            scoped = _scoped(settings, directory.name)
            ns = argparse.Namespace(deliver="mail", yes=args.yes, force=True, dry_run=args.dry_run)
            cmd_issue(scoped, ns)
        else:
            print(f"{directory.name}: vigente, no se renueva")
    return EXIT_OK


def _scoped(settings: Settings, domain: str) -> Settings:
    clone = Settings()
    clone.__dict__.update(settings.__dict__)
    clone.domain = domain
    return clone


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mail_cert.py",
        description="Certificado TLS interno para docker-mailserver (A.SPEC 0028).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="estado del certificado y de la entrega")
    sub.add_parser("check", help="verifica cadena, entrega y TLS en los tres puertos")

    issue = sub.add_parser("issue", help="emite (si hace falta) y entrega")
    issue.add_argument("--deliver", choices=["mail", "none"], default="mail")
    issue.add_argument("--force", action="store_true", help="reemitir aunque esté vigente")
    issue.add_argument("--dry-run", action="store_true", help="muestra, no escribe")
    issue.add_argument("--yes", action="store_true", help="confirma la recreación del contenedor")

    renew = sub.add_parser("renew", help="renueva si está por debajo del umbral")
    renew.add_argument("--all", action="store_true", help="todos los certificados gestionados")
    renew.add_argument("--force", action="store_true")
    renew.add_argument("--dry-run", action="store_true")
    renew.add_argument("--yes", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    if not DOMAIN_RE.match(settings.domain):
        print(f"error: dominio inválido: {settings.domain}", file=sys.stderr)
        return EXIT_VALIDATION
    try:
        if args.command == "status":
            return cmd_status(settings)
        if args.command == "check":
            return cmd_check(settings)
        if args.command == "issue":
            return cmd_issue(settings, args)
        if args.command == "renew":
            return cmd_renew(settings, args)
    except MailCertError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        print("interrumpido", file=sys.stderr)
        return 130
    return EXIT_VALIDATION


if __name__ == "__main__":
    raise SystemExit(main())
