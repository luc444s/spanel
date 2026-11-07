#!/usr/bin/env python3
"""SPanel: auto-reparación TLS/rutas de Traefik, agnóstica al server (A.SPEC 0044).

Detecta (y con ``--fix`` repara) los fallos deterministas conocidos de las
rutas ``spanel-*.yml`` publicadas en el dynamic-config de Traefik:

* R1: router HTTP que intercepta ``/.well-known/acme-challenge`` (la ``rule``
  no tiene la exclusión) → parche atómico con ``.bak.<ts>`` al lado.
* R2: cert por defecto de Traefik sirviendo un dominio → si el DNS público NO
  apunta a la IP pública del server: PENDIENTE (el A record lo arregla un
  humano; nunca se reinicia); si apunta bien: restart de Traefik para
  re-disparar el challenge, con cooldown persistido en un state file.
* R3: router websecure sin ``tls.certResolver`` → parche + restart.
* R4: archivo huérfano cuyo contenedor ya no existe → moverlo a
  ``<config-dir>/.orphans/`` (nunca borrar).

Agnóstico por autodetección (flag > env > detect > error): contenedor Traefik
(por imagen/command de ``docker ps``, no por nombre fijo), directorio dynamic
(``--providers.file.directory`` del contenedor o de su traefik.yml), resolvers
(todos los ``certificatesResolvers`` de traefik.yml, sin asumir nombres),
dominios (todos los routers publicados en ``spanel-*.yml``) y la IP pública
vía socket saliente (fallback SPANEL_PUBLIC_IP / --public-ip). El CLI docker
respeta ``DOCKER_HOST`` naturalmente.

Modos:
  check (default) → solo reporta; exit 0 ok / 1 pendientes / 2 error.
  --fix           → ejecuta acciones seguras (parches, restart con cooldown,
                    orphans). Sin --fix no escribe ni reinicia nada.
  --json          → salida máquina-parsable (y log JSON-lines).

stdlib only, sin pip, sin APIs externas.

Instalación en server (la ejecuta el operador):

    scp scripts/spanel-repair.py scripts/systemd/spanel-repair.service \\
        scripts/systemd/spanel-repair.timer root@server:/opt/spanel/
    ssh root@server 'install -m 755 /opt/spanel/spanel-repair.py /usr/local/sbin/ ;
      cp /opt/spanel/spanel-repair.service /opt/spanel/spanel-repair.timer /etc/systemd/system/ ;
      systemctl daemon-reload && systemctl enable --now spanel-repair.timer'

Si systemd no existe (o no es root), corre manual y sale igual.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

FILE_PREFIX = "spanel-"
HOST_RE = re.compile(r"Host\(`([^`]+)`\)")
ACME_PATH = "/.well-known/acme-challenge"
CHALLENGE_EXCLUSION = f"!(PathPrefix(`{ACME_PATH}`))"
DEFAULT_COOLDOWN = 3600
EXIT_OK = 0
EXIT_PENDING = 1
EXIT_ERROR = 2


class RepairError(Exception):
    """Error controlado; main lo convierte en hallazgo y exit code estable."""


def utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Tooling inyectable: docker CLI, DNS, TLS, IP pública, reloj. Todo fake-able.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Result:
    stdout: str
    stderr: str = ""
    returncode: int = 0


def docker_cli(args: list[str]) -> Result:
    """Docker CLI real (respeta DOCKER_HOST del entorno)."""
    try:
        proc = subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Result("", f"docker CLI indisponible: {exc}", 127)
    return Result(proc.stdout, proc.stderr, proc.returncode)


def openssl_issuer(host: str, port: int = 443) -> str | None:
    """Issuer del certificado servido en host:port (openssl real)."""
    proc = subprocess.run(
        ["openssl", "s_client", "-connect", f"{host}:{port}", "-servername", host],
        input="\n",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    out = proc.stdout + "\n" + proc.stderr
    match = re.search(r"issuer=.*", out)
    return match.group(0).strip() if match else None


def resolve_dns(host: str) -> str | None:
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


def public_ip_socket() -> str | None:
    """IP local de salida vía socket UDP conectado (sin curl ni APIs externas)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


@dataclass
class Tooling:
    """Puntos de inyección para tests: docker, DNS, TLS, IP pública, reloj."""

    docker: Callable[[list[str]], Result] = docker_cli
    dns: Callable[[str], str | None] = resolve_dns
    tls_issuer: Callable[[str], str | None] = openssl_issuer
    public_ip: Callable[[], str | None] = public_ip_socket
    now: Callable[[], float] = time.time


# --------------------------------------------------------------------------- #
# Autodetección (I1: nada hardcodeado de un server concreto)
# --------------------------------------------------------------------------- #


@dataclass
class Detection:
    traefik_container: str
    config_dir: str
    resolvers: list[str]
    traefik_yml_text: str = ""


def detect_traefik_container(
    tooling: Tooling, override: str | None = None
) -> str | None:
    """Contenedor con imagen `traefik` o command `traefik*` (sin nombres fijos)."""
    if override:
        return override
    out = tooling.docker(["ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Command}}"])
    if out.returncode != 0:
        raise RepairError(f"docker ps falló: {out.stderr.strip() or out.stdout.strip()}")
    for line in out.stdout.splitlines():
        parts = [p.strip() for p in line.split("\t")]
        while len(parts) < 3:
            parts.append("")
        name, image, command = parts[0], parts[1], parts[2]
        if "traefik" in image.lower() or command.strip().startswith("traefik"):
            return name
    return None


def dir_from_traefik_yml(text: str) -> str | None:
    match = re.search(r"^\s*directory:\s*([^\s#]+)", text or "", re.MULTILINE)
    return match.group(1).strip("\"'") if match else None


def resolvers_from_traefik_yml(text: str) -> list[str]:
    """Todos los `certificatesResolvers` del traefik.yml; sin nombres asumidos."""
    if not text:
        return []
    lines = text.splitlines()
    found: list[str] = []
    for idx, line in enumerate(lines):
        m = re.match(r"^(\s*)certificatesResolvers:\s*(?:#.*)?$", line)
        if not m:
            continue
        base_indent = len(m.group(1))
        child_indent: int | None = None
        for child in lines[idx + 1 :]:
            cm = re.match(r"^(\s*)([^#?\s][^:]*):\s*(?:#.*)?$", child)
            if not cm:
                continue
            if len(cm.group(1)) <= base_indent:
                break
            if child_indent is None:
                child_indent = len(cm.group(1))
            if len(cm.group(1)) == child_indent:
                name = cm.group(2).strip()
                if name not in found:
                    found.append(name)
        break
    return found


def inspect_traefik(tooling: Tooling, container: str) -> dict:
    out = tooling.docker(["inspect", container])
    if out.returncode != 0:
        raise RepairError(f"docker inspect {container} falló: {out.stderr.strip()}")
    try:
        return json.loads(out.stdout)[0]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
        raise RepairError(f"docker inspect no devolvió JSON válido: {exc}") from exc


def traefik_yml_text(tooling: Tooling, container: str, ins: dict) -> str:
    """El traefik.yml del contenedor: del HostPath montado, o `docker exec cat`."""
    for mount in ins.get("Mounts", []):
        dest = str(mount.get("Destination", ""))
        if dest.rstrip("/").endswith(("traefik.yml", "traefik.yaml")):
            source = mount.get("Source")
            if source:
                try:
                    return Path(source).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
    for path in ("/etc/traefik/traefik.yml", "/etc/traefik/traefik.yaml", "/traefik.yml"):
        out = tooling.docker(["exec", container, "cat", path])
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
    return ""


def host_path_for_mount(ins: dict, path: str) -> str:
    """Traduce un path del contenedor a su Source en el host si hay un mount
    que lo cubra (exacto o como prefijo de un dir). Sin mount => igual."""
    target = path.rstrip("/") or "/"
    best: tuple[int, str] | None = None  # (len(dest), host_path)
    for mount in ins.get("Mounts", []):
        dest = str(mount.get("Destination", "")).rstrip("/") or "/"
        source = mount.get("Source")
        if not source:
            continue
        if target == dest or (dest != "/" and target.startswith(dest + "/")):
            rel = target[len(dest):].lstrip("/")
            host = paths_join(str(source), rel)
            if best is None or len(dest) > best[0]:
                best = (len(dest), host)
    if best is not None:
        return best[1]
    return path


def paths_join(base: str, rel: str) -> str:
    return f"{base.rstrip('/')}/{rel}" if rel else base


def detect(
    tooling: Tooling,
    traefik_override: str | None = None,
    config_dir_override: str | None = None,
    env: dict[str, str] | None = None,
) -> Detection | None:
    """Configuración efectiva: flag > env > detect > error. None si no hay Traefik."""
    env = dict(os.environ if env is None else env)
    container = detect_traefik_container(
        tooling, traefik_override or env.get("SPANEL_TRAEFIK_CONTAINER")
    )
    if not container:
        return None

    ins = inspect_traefik(tooling, container)
    yml_text = traefik_yml_text(tooling, container, ins)

    dir_from_args: str | None = None
    tokens = [*ins.get("Args", []), *ins.get("Config", {}).get("Cmd", [])]
    for token in tokens:
        if token.startswith("--providers.file.directory="):
            dir_from_args = token.split("=", 1)[1].strip().strip('"')
    if not dir_from_args and "--providers.file.directory" in tokens:
        idx = tokens.index("--providers.file.directory")
        if idx + 1 < len(tokens):
            dir_from_args = tokens[idx + 1].strip().strip('"')

    config_dir = (
        config_dir_override
        or env.get("SPANEL_DYNAMIC_DIR")
        or dir_from_args
        or dir_from_traefik_yml(yml_text)
    )
    if not config_dir:
        raise RepairError(
            "no se detectó el dynamic-config dir (ni --providers.file.directory "
            "en el contenedor, ni providers.file.directory en su traefik.yml); "
            "usá --config-dir o SPANEL_DYNAMIC_DIR"
        )

    # El dir detectado vive en el SISTEMA DE ARCHIVOS DEL CONTENEDOR; el script
    # corre en el host. Si hay un mount cuyo Destination lo cubre, usar su
    # Source (el path real del host). Caso real: /opt/stacks/traefik/config
    # montado como /config.
    host_dir = host_path_for_mount(ins, config_dir)
    if host_dir != config_dir and Path(host_dir).is_dir():
        config_dir = host_dir

    return Detection(
        traefik_container=container,
        config_dir=config_dir,
        resolvers=resolvers_from_traefik_yml(yml_text),
        traefik_yml_text=yml_text,
    )


# --------------------------------------------------------------------------- #
# Mini-parser YAML de línea (los spanel-*.yml son simples; sin pyyaml, I6)
# --------------------------------------------------------------------------- #


@dataclass
class Router:
    name: str
    key_idx: int  # índice de la línea del nombre del router
    key_indent: int
    content_indent: int = 0  # indent de las keys del router (rule/entryPoints/...)
    rule_idx: int | None = None
    rule_value: str | None = None
    entrypoints: list[str] = field(default_factory=list)
    tls_key_idx: int | None = None
    tls_flow: str | None = None  # valor inline de `tls: {...}`
    resolver_idx: int | None = None
    resolver_value: str | None = None


KEY_RE = re.compile(r"^(\s*)([\w.-]+):\s*(?:([^\n#].*)?)?$")
ITEM_RE = re.compile(r"^(\s*)-\s*(.*)$")


def parse_routers(text: str) -> list[Router]:
    """Routers del bloque `http: routers:` de un documento — line-based."""
    lines = text.splitlines()
    routers_indent = -1
    for idx, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s*)routers:\s*(?:#.*)?$", line)
        if m and routers_indent < 0:
            routers_indent = len(m.group(1))
    if routers_indent < 0:
        return []

    routers: list[Router] = []
    current: Router | None = None
    pending_key: str | None = None
    for idx in range(routers_indent + 1, len(lines)):
        raw = lines[idx]
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent <= routers_indent:
            break
        im = ITEM_RE.match(raw)
        km = KEY_RE.match(raw)
        if im and not km:
            if pending_key == "entryPoints" and current is not None:
                value = im.group(2).strip().strip("\"'")
                if value:
                    current.entrypoints.append(value)
            continue
        if km:
            key, value = km.group(2), km.group(3)
            if current is None:
                current = Router(name=key, key_idx=idx, key_indent=indent)
                pending_key = None
                continue
            if indent == current.key_indent:
                routers.append(current)
                current = Router(name=key, key_idx=idx, key_indent=indent)
                pending_key = None
                continue
            if not current.content_indent:
                current.content_indent = indent
            if key == "entryPoints" and value and value.startswith("["):
                current.entrypoints.extend(
                    p.strip().strip("\"'")
                    for p in value.strip("[]").split(",")
                    if p.strip()
                )
                pending_key = key
                continue
            if key == "tls":
                current.tls_key_idx = idx
                current.tls_flow = value.strip() if value else None
                pending_key = "tls"
                continue
            if key == "certResolver" and pending_key == "tls" and value:
                current.resolver_idx = idx
                current.resolver_value = value.strip("\"'")
                pending_key = None
                continue
            if key == "rule":
                current.rule_idx = idx
                current.rule_value = (value or "").strip()
                pending_key = key
            else:
                pending_key = key
    if current is not None:
        routers.append(current)
    return routers


def parse_tls_flow(flow: str | None) -> str | None:
    """`tls: {certResolver: x}` → `x`; None si no hay resolver inline."""
    if not flow or not flow.startswith("{"):
        return None
    m = re.search(r"certResolver:\s*([^,}]+)", flow)
    return m.group(1).strip().strip("\"'") if m else None


def resolver_of(router: Router) -> str | None:
    return router.resolver_value or (parse_tls_flow(router.tls_flow) if router.tls_flow else None)


# --------------------------------------------------------------------------- #
# Parches YAML atómicos (tmp + os.replace, .bak con timestamp, I4)
# --------------------------------------------------------------------------- #


def atomic_patch(path: Path, transform: Callable[[list[str]], list[str] | None]) -> bool:
    """Escribe `transform(lines)` si hay cambio: .bak.<ts> primero, tmp+replace."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=False)
    patched = transform(list(lines))
    if patched is None:
        return False
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(f"{path.name}.bak.{ts}")
    backup.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as tmp:
        tmp.write("\n".join(patched) + "\n")
        tmp.flush()
        os.chmod(tmp.name, path.stat().st_mode)
    os.replace(tmp.name, path)
    return True


def patch_challenge_exclusion(lines: list[str]) -> list[str] | None:
    """R1: agrega la exclusión de acme-challenge a cada router HTTP que falte."""
    routers = parse_routers("\n".join(lines))
    patches: dict[int, str] = {}
    for router in routers:
        if "web" not in router.entrypoints or router.rule_idx is None:
            continue
        if router.rule_value is None or has_challenge_exclusion(router.rule_value):
            continue
        match = KEY_RE.match(lines[router.rule_idx])
        ind = len(match.group(1))
        new_value = f"({router.rule_value}) && {CHALLENGE_EXCLUSION}"
        patches[router.rule_idx] = f"{' ' * ind}rule: {new_value}"
    if not patches:
        return None
    return [patches.get(idx, line) for idx, line in enumerate(lines)]


def has_challenge_exclusion(rule: str) -> bool:
    return (
        f"!PathPrefix(`{ACME_PATH}`)" in rule
        or f"!(PathPrefix(`{ACME_PATH}`))" in rule
    )


def _router_block_end(lines: list[str], router: Router) -> int:
    end = len(lines)
    for idx in range(router.key_idx + 1, len(lines)):
        raw = lines[idx]
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if len(raw) - len(raw.lstrip()) <= router.key_indent:
            return idx
    return end


def patch_add_resolver(lines: list[str], router_name: str, resolver: str) -> list[str] | None:
    """R3: inserta `tls:` + `certResolver:` al final del bloque del router."""
    routers = parse_routers("\n".join(lines))
    target = next((r for r in routers if r.name == router_name), None)
    if target is None or resolver_of(target):
        return None
    end = _router_block_end(lines, target)
    ind = " " * target.key_indent
    insert = [f"{ind}tls:", f"{ind}  certResolver: {resolver}"]
    return [*lines[:end], *insert, *lines[end:]]


# --------------------------------------------------------------------------- #
# Hallazgos
# --------------------------------------------------------------------------- #


@dataclass
class Finding:
    rule: str  # R1..R4 / RESTART / ENV
    target: str  # archivo:router:host
    status: str  # ok | pendiente | planned | fixed
    action: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "target": self.target,
            "status": self.status,
            "action": self.action,
            "detail": self.detail,
            "ts": utc_ts(),
        }


def findings_exit(findings: list[Finding]) -> int:
    if any(f.status in ("pendiente", "planned", "fixed") for f in findings):
        return EXIT_PENDING
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Cooldown (I3): nunca dos restarts dentro del cooldown
# --------------------------------------------------------------------------- #


def load_state(state_file: Path) -> dict:
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state_file: Path, data: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_name(state_file.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, state_file)


def maybe_restart(
    tooling: Tooling,
    container: str,
    cooldown: int,
    state_file: Path,
    dry_run: bool,
    reason: str,
) -> Finding:
    last, data = load_state(state_file), {}
    state = last
    last_ts = state.get("last_restart_ts")
    if dry_run:
        return Finding("RESTART", container, "planned", f"docker restart {container} ({reason})")
    if isinstance(last_ts, int | float) and tooling.now() - float(last_ts) < cooldown:
        remaining = int(cooldown - (tooling.now() - float(last_ts)))
        return Finding(
            "RESTART",
            container,
            "pendiente",
            "skip: cooldown activo",
            f"faltan {remaining}s (reason: {reason})",
        )
    out = tooling.docker(["restart", container])
    if out.returncode != 0:
        return Finding(
            "RESTART", container, "pendiente", "docker restart falló", out.stderr.strip()
        )
    save_state(state_file, {**data, **state, "last_restart_ts": tooling.now(), "last_reason": reason})
    return Finding("RESTART", container, "fixed", f"docker restart {container}", reason)


# --------------------------------------------------------------------------- #
# Reglas R1-R4
# --------------------------------------------------------------------------- #


def document_container(text: str) -> str | None:
    m = re.search(r"url:\s*https?://([^:/\"\s]+)", text)
    return m.group(1) if m else None


def collect_site_files(config_dir: Path, only_host: str | None) -> list[Path]:
    try:
        files = sorted(
            p
            for p in config_dir.iterdir()
            if p.is_file()
            and p.name.startswith(FILE_PREFIX)
            and p.suffix in (".yml", ".yaml")
        )
    except OSError:
        return []
    if not only_host:
        return files
    kept: list[Path] = []
    for path in files:
        try:
            if only_host in HOST_RE.findall(path.read_text(encoding="utf-8", errors="replace")):
                kept.append(path)
        except OSError:
            continue
    return kept


def rule_r1(path: Path, text: str, fix: bool) -> tuple[list[Finding], list[Path]]:
    findings: list[Finding] = []
    patched: list[Path] = []
    for router in parse_routers(text):
        if "web" not in router.entrypoints or router.rule_idx is None:
            continue
        if router.rule_value and has_challenge_exclusion(router.rule_value):
            findings.append(Finding("R1", f"{path.name}:{router.name}", "ok", "-", "exclusion OK"))
            continue
        new_value = f"({router.rule_value}) && {CHALLENGE_EXCLUSION}"
        if fix:
            changed = atomic_patch(path, patch_challenge_exclusion)
            if changed:
                patched.append(path)
            findings.append(
                Finding(
                    "R1",
                    f"{path.name}:{router.name}",
                    "fixed" if changed else "ok",
                    "patch rule con exclusión acme-challenge (atomic, .bak)",
                    f"rule: {new_value}",
                )
            )
        else:
            findings.append(
                Finding(
                    "R1",
                    f"{path.name}:{router.name}",
                    "planned",
                    "patch rule con exclusión acme-challenge (necesita --fix)",
                    f"rule: {new_value}",
                )
            )
    return findings, patched


def rule_r2(
    path: Path,
    text: str,
    tooling: Tooling,
    public_ip: str | None,
) -> list[Finding]:
    """Cert default: DNS bien → restart en cola (con --fix y cooldown);
    DNS mal → PENDIENTE, jamás reinicia (I5)."""
    findings: list[Finding] = []
    for router in parse_routers(text):
        eps = router.entrypoints
        websecure = "websecure" in eps or (resolver_of(router) and "web" not in eps)
        if not websecure or not resolver_of(router):
            continue
        hosts = HOST_RE.findall(router.rule_value or "")
        if not hosts:
            continue
        host = hosts[0]
        target = f"{path.name}:{router.name}:{host}"
        try:
            issuer = tooling.tls_issuer(host) or ""
        except Exception as exc:  # noqa: BLE001 - openssl puede fallar de muchas formas
            findings.append(Finding("R2", target, "pendiente", "probe TLS falló", repr(exc)))
            continue
        if "TRAEFIK DEFAULT CERT" not in issuer.upper():
            findings.append(Finding("R2", target, "ok", "-", f"issuer real: {issuer[:80]}"))
            continue
        resolved = tooling.dns(host)
        if not resolved or (public_ip and resolved != public_ip):
            findings.append(
                Finding(
                    "R2",
                    target,
                    "pendiente",
                    "PENDIENTE: arreglar el A record (no se reinicia)",
                    (
                        f"cert default sirviendo; DNS de '{host}' -> {resolved!r}, "
                        f"IP pública del server -> {public_ip!r}"
                    ),
                )
            )
        else:
            findings.append(
                Finding(
                    "RESTART",
                    target,
                    "ok",
                    "restart de traefik en cola con cooldown",
                    f"DNS '{host}' -> {resolved} == IP pública (default cert)",
                )
            )
    return findings


def rule_r3(
    path: Path, text: str, resolver: str | None, fix: bool
) -> tuple[list[Finding], list[Path]]:
    findings: list[Finding] = []
    patched: list[Path] = []
    for router in parse_routers(text):
        if "websecure" not in router.entrypoints or resolver_of(router):
            continue
        if resolver is None:
            findings.append(
                Finding(
                    "R3",
                    f"{path.name}:{router.name}",
                    "pendiente",
                    "PENDIENTE: no hay certificatesResolvers en traefik.yml; "
                    "definir uno es trabajo humano",
                )
            )
            continue
        if fix:
            changed = atomic_patch(
                path,
                lambda lines, name=router.name, res=resolver: patch_add_resolver(
                    lines, name, res
                ),
            )
            if changed:
                patched.append(path)
            findings.append(
                Finding(
                    "R3",
                    f"{path.name}:{router.name}",
                    "fixed" if changed else "ok",
                    f"patch tls.certResolver = {resolver} (atomic, .bak)",
                )
            )
        else:
            findings.append(
                Finding(
                    "R3",
                    f"{path.name}:{router.name}",
                    "planned",
                    f"patch tls.certResolver = {resolver} (necesita --fix)",
                )
            )
    return findings, patched


def rule_r4(
    path: Path, text: str, running: list[str], config_dir: Path, fix: bool
) -> Finding | None:
    container = document_container(text)
    if not container:
        return None
    if any(name == container for name in running):
        return Finding("R4", f"{path.name}:{container}", "ok", "-", "container existe")
    if fix:
        orphans = config_dir / ".orphans"
        orphans.mkdir(parents=True, exist_ok=True)
        dest = orphans / path.name
        try:
            path.replace(dest)
        except OSError as exc:
            return Finding("R4", f"{path.name}:{container}", "pendiente", "mover falló", str(exc))
        return Finding("R4", f"{path.name}:{container}", "fixed", f"movido a {dest} (no borrado)")
    return Finding(
        "R4", f"{path.name}:{container}", "planned", "mover a .orphans/ (necesita --fix)"
    )


# --------------------------------------------------------------------------- #
# Orquestación
# --------------------------------------------------------------------------- #


def public_ip_value(tooling: Tooling, args: argparse.Namespace) -> str | None:
    return args.public_ip or os.environ.get("SPANEL_PUBLIC_IP") or tooling.public_ip()


def choose_resolver(det: Detection, texts: list[Path], args: argparse.Namespace) -> str | None:
    """R3: el referido por otros routers spanel, o el único existente, o override."""
    if args.resolver:
        return args.resolver
    used: list[str] = []
    for path in texts:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for router in parse_routers(text):
            res = resolver_of(router)
            if res and res not in used:
                used.append(res)
    if len(used) == 1:
        return used[0]
    if not used and len(det.resolvers) == 1:
        return det.resolvers[0]
    return None


def check_host(
    args: argparse.Namespace, tooling: Tooling
) -> tuple[list[Finding], int]:
    fix = bool(args.fix)
    findings: list[Finding] = []

    try:
        det = detect(
            tooling,
            traefik_override=args.traefik_container,
            config_dir_override=args.config_dir,
        )
    except RepairError as exc:
        return [Finding("ENV", "traefik", "pendiente", str(exc))], EXIT_PENDING

    if det is None:
        findings.append(
            Finding(
                "ENV",
                "traefik",
                "pendiente",
                "PENDIENTE: no se encontró contenedor traefik (docker ps). "
                "usá --traefik-container o SPANEL_TRAEFIK_CONTAINER",
            )
        )
        return findings, EXIT_PENDING

    config_dir = Path(det.config_dir).expanduser()
    if not config_dir.is_dir():
        findings.append(
            Finding(
                "ENV",
                det.config_dir,
                "pendiente",
                "PENDIENTE: el dynamic-config dir detectado no existe en este host",
                f"{config_dir}",
            )
        )
        return findings, EXIT_PENDING

    files = collect_site_files(config_dir, args.only_host)
    running = docker_ps_names(tooling)
    texts: dict[Path, str] = {}
    for path in files:
        try:
            texts[path] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

    resolver = choose_resolver(det, list(texts), args)

    patched: list[Path] = []
    pending_restart = False
    for path, text in texts.items():
        f_findings, f_patched = rule_r1(path, text, fix)
        findings.extend(f_findings)
        patched.extend(f_patched)
        for f in rule_r2(path, text, tooling, public_ip_value(tooling, args)):
            if f.rule == "RESTART":
                pending_restart = True
            else:
                findings.append(f)
        f3_findings, f3_patched = rule_r3(path, text, resolver, fix)
        findings.extend(f3_findings)
        patched.extend(f3_patched)
        f4 = rule_r4(path, text, running, config_dir, fix)
        if f4:
            findings.append(f4)
        # re-leer si hubo parche para no re-parchear el mismo router
        if path in patched:
            try:
                texts[path] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass

    if patched or pending_restart:
        reason = "R2 re-disparar challenge" if not patched else (
            f"parches: {', '.join(p.name for p in patched)}"
        )
        state_file = Path(
            args.state_file
            or os.environ.get("SPANEL_STATE_FILE")
            or str(config_dir / ".spanel-repair-state.json")
        )
        findings.append(
            maybe_restart(tooling, det.traefik_container, args.cooldown, state_file, not fix, reason)
        )

    return findings, findings_exit(findings)


def docker_ps_names(tooling: Tooling) -> list[str]:
    out = tooling.docker(["ps", "--format", "{{.Names}}"])
    if out.returncode != 0:
        raise RepairError(f"docker ps falló: {out.stderr.strip() or out.stdout.strip()}")
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# Salida, log y CLI
# --------------------------------------------------------------------------- #


def format_table(findings: list[Finding]) -> str:
    if not findings:
        return "sin hallazgos: todo OK"
    width = max(len(f.target) for f in findings)
    out = ["RULE    STATUS    TARGET" + " " * max(width - 6, 1) + "ACTION"]
    for f in findings:
        out.append(
            f"{f.rule:<7} {f.status:<9} {f.target:<{width}} {f.action}"
            + (f"\n        -> {f.detail}" if f.detail else "")
        )
    return "\n".join(out)


def log_path_for(config_dir: Path | None, override: str | None) -> Path | None:
    if override:
        return Path(override).expanduser()
    env = os.environ.get("SPANEL_LOG_FILE")
    if env:
        return Path(env).expanduser()
    candidates = [Path("/var/log/spanel-repair.log")]
    if config_dir:
        candidates.append(config_dir / ".spanel-repair.log")
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            return path
        except OSError:
            continue
    return None


def write_log(path: Path | None, findings: list[Finding], json_mode: bool) -> None:
    if path is None:
        return
    try:
        with path.open("a", encoding="utf-8") as handle:
            for f in findings:
                payload = json.dumps(f.as_dict(), ensure_ascii=False)
                if json_mode:
                    handle.write(payload + "\n")
                else:
                    handle.write(
                        f"{utc_ts()} rule={f.rule} target={f.target} "
                        f"status={f.status} action={f.action} detail={f.detail}\n"
                    )
    except OSError:
        pass  # log best-effort: nunca crash por el log


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spanel-repair.py",
        description="Auto-reparación TLS/rutas de Traefik de SPanel (A.SPEC 0044).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--fix", action="store_true", help="ejecutar acciones (default: dry-run)")
    parser.add_argument("--json", action="store_true", help="salida máquina-parsable")
    parser.add_argument("--traefik-container", default=None, help="override (auto: imagen/command)")
    parser.add_argument("--config-dir", default=None, help="override del dynamic-config dir")
    parser.add_argument("--resolver", default=None, help="override del certResolver para R3")
    parser.add_argument("--public-ip", default=None, help="override de la IP pública")
    parser.add_argument("--only-host", default=None, help="enfocar una falla por host")
    parser.add_argument("--cooldown", type=int, default=DEFAULT_COOLDOWN, help="s entre restarts (default 3600)")
    parser.add_argument("--state-file", default=None, help="estado del cooldown")
    parser.add_argument("--log-file", default=None, help="log único (default /var/log/spanel-repair.log)")
    return parser


def main(argv: list[str] | None = None, tooling: Tooling | None = None) -> int:
    tooling = tooling or Tooling()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cooldown < 0:
        parser.error("--cooldown debe ser >= 0")

    try:
        findings, code = check_host(args, tooling)
    except RepairError as exc:
        findings = [Finding("ENV", "error", "pendiente", str(exc))]
        code = EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - nunca crash
        findings = [Finding("ENV", "error", "pendiente", "error inesperado", repr(exc))]
        code = EXIT_ERROR

    if args.json:
        print(
            json.dumps(
                {"ts": utc_ts(), "exit": code, "findings": [f.as_dict() for f in findings]},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(format_table(findings))
        print(f"exit={code}")

    log_dir = Path(args.config_dir).expanduser() if args.config_dir else None
    write_log(log_path_for(log_dir, args.log_file), findings, args.json)
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
