"""Primitivas puras y transporte del CLI de dominios (sin Docker)."""

from __future__ import annotations

import os
import re
import shlex
import socket
import ssl
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import yaml

from .config import Settings

EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_DNS = 3
EXIT_TRANSPORT = 4
EXIT_CERT = 5
EXIT_WORDPRESS = 6

MARKER = "# managed-by: spanel"
FILE_PREFIX = "spanel-"
# TLDs que no existen en la raíz pública de DNS; el perfil público los rechaza.
INTERNAL_TLDS = {
    "spanel",
    "internal",
    "local",
    "localhost",
    "lan",
    "home",
    "lab",
    "test",
    "example",
    "invalid",
}
LABEL_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
BACKTICK_HOST_RE = re.compile(r"Host\(`([^`]+)`\)")


class ProxyError(Exception):
    """Error controlado con código de salida estable."""

    def __init__(self, message: str, exit_code: int = EXIT_VALIDATION) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# ---- dominio y nombres ----------------------------------------------------


def normalize_domain(raw: str, profile: str = "internal") -> str:
    """Valida y normaliza un FQDN interno o público.

    Acepta minúsculas, sin esquema, sin puerto y sin rutas. El perfil interno
    permite TLDs no delegados (`spanel`); el público exige un TLD con letras.
    """
    value = (raw or "").strip().lower()
    if not value:
        raise ProxyError("el dominio no puede estar vacío", EXIT_VALIDATION)
    value = value.removesuffix(".")
    if any(token in value for token in ("/", ":", "*", " ", "\t")):
        raise ProxyError(
            "dominio inválido: sin esquema, puerto, ruta ni comodines", EXIT_VALIDATION
        )
    labels = value.split(".")
    if len(labels) < 2:
        raise ProxyError("dominio inválido: hace falta al menos nombre y TLD", EXIT_VALIDATION)
    if any(not label for label in labels):
        raise ProxyError("dominio inválido: etiqueta vacía", EXIT_VALIDATION)
    if len(value) > 253:
        raise ProxyError("dominio inválido: supera 253 caracteres", EXIT_VALIDATION)
    for label in labels:
        if not LABEL_RE.fullmatch(label):
            raise ProxyError(
                f"dominio inválido: la etiqueta '{label}' no es DNS válida", EXIT_VALIDATION
            )
        if label.startswith("xn--"):
            raise ProxyError(
                "dominio inválido: usá el nombre Unicode, no punycode", EXIT_VALIDATION
            )
    tld = labels[-1]
    if tld.isdigit():
        raise ProxyError("dominio inválido: el TLD no puede ser numérico", EXIT_VALIDATION)
    if profile == "public" and (tld in INTERNAL_TLDS or any(char.isdigit() for char in tld)):
        raise ProxyError(
            f"dominio inválido para perfil público: '{tld}' no es un TLD público",
            EXIT_VALIDATION,
        )
    return value


def slug_for(container: str) -> str:
    """Nombre corto y estable derivado del contenedor del sitio."""
    slug = re.sub(r"[^a-z0-9]+", "-", container.strip().lower()).strip("-")
    slug = slug.removeprefix(FILE_PREFIX).removesuffix("-wp").strip("-")
    if not slug:
        raise ProxyError(f"no se pudo derivar un nombre para '{container}'", EXIT_VALIDATION)
    return slug


def site_file_name(slug: str) -> str:
    return f"{FILE_PREFIX}{slug}.yml"


def router_rule(domains: Iterable[str]) -> str:
    hosts = list(dict.fromkeys(domains))
    if not hosts:
        raise ProxyError("el sitio no tiene dominios", EXIT_VALIDATION)
    return " || ".join(f"Host(`{host}`)" for host in hosts)


def render_site_yaml(
    container: str,
    domains: Sequence[str],
    resolver: str,
    entrypoint_http: str = "web",
    entrypoint_https: str = "websecure",
) -> str:
    slug = slug_for(container)
    rule = router_rule(domains)
    service = f"{FILE_PREFIX}{slug}"
    doc = {
        "http": {
            "routers": {
                f"{service}-http": {
                    "rule": rule,
                    "entryPoints": [entrypoint_http],
                    "middlewares": [f"{service}-redirect"],
                    "service": service,
                    "priority": 200,
                },
                service: {
                    "rule": rule,
                    "entryPoints": [entrypoint_https],
                    "service": service,
                    "tls": {"certResolver": resolver},
                    "priority": 100,
                },
            },
            "middlewares": {
                f"{service}-redirect": {
                    "redirectScheme": {"scheme": "https", "permanent": True},
                }
            },
            "services": {
                service: {
                    "loadBalancer": {"servers": [{"url": f"http://{container}:80"}]}
                }
            },
        }
    }
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"{MARKER}\n{body}"


def is_managed_document(text: str) -> bool:
    return MARKER in text


def parse_site_document(text: str) -> dict:
    if not is_managed_document(text):
        raise ProxyError("el archivo YAML no está gestionado por spanel", EXIT_VALIDATION)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:  # pragma: no cover - defensivo
        raise ProxyError(f"YAML inválido: {exc}", EXIT_VALIDATION) from exc
    if not isinstance(data, dict) or "http" not in data:
        raise ProxyError("el YAML no tiene la estructura esperada", EXIT_VALIDATION)
    return data


def document_domains(data: dict) -> list[str]:
    routers = (data.get("http") or {}).get("routers") or {}
    found: list[str] = []
    for router in routers.values():
        if not isinstance(router, dict):
            continue
        rule = router.get("rule") or ""
        for host in BACKTICK_HOST_RE.findall(rule):
            if host not in found:
                found.append(host)
    return found


def document_container(data: dict) -> str | None:
    services = (data.get("http") or {}).get("services") or {}
    for service in services.values():
        servers = ((service or {}).get("loadBalancer") or {}).get("servers") or []
        for server in servers:
            url = (server or {}).get("url") or ""
            match = re.match(r"^https?://([^:/]+)", url)
            if match:
                return match.group(1)
    return None


def document_resolver(data: dict) -> str | None:
    routers = (data.get("http") or {}).get("routers") or {}
    for router in routers.values():
        tls = (router or {}).get("tls") if isinstance(router, dict) else None
        if isinstance(tls, dict) and tls.get("certResolver"):
            return str(tls["certResolver"])
    return None


# ---- archivos -------------------------------------------------------------


def atomic_write(path: Path, text: str, mode: int = 0o644) -> None:
    """Escritura atómica en el mismo directorio (temp + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def validate_managed_path(path: Path) -> None:
    if not path.name.startswith(FILE_PREFIX) or path.suffix not in (".yml", ".yaml"):
        raise ProxyError(f"nombre de archivo no gestionado: {path.name}", EXIT_VALIDATION)


# ---- transporte -----------------------------------------------------------


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def text(self) -> str:
        return self.stdout if self.stdout.strip() else self.stderr


class Runner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        check: bool = False,
        stdin: str | None = None,
        timeout: int | None = 120,
    ) -> CommandResult: ...


class LocalRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        check: bool = False,
        stdin: str | None = None,
        timeout: int | None = 120,
    ) -> CommandResult:
        result = subprocess.run(
            list(argv),
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        command = CommandResult(list(argv), result.returncode, result.stdout, result.stderr)
        if check and not command.ok:
            raise ProxyError(
                f"falló {' '.join(argv)}: {command.text().strip()}", EXIT_TRANSPORT
            )
        return command


class SshRunner:
    def __init__(self, host: str, user: str | None, port: int, password: str | None) -> None:
        self.host = host
        self.user = user
        self.port = port
        self.password = password

    def run(
        self,
        argv: Sequence[str],
        *,
        check: bool = False,
        stdin: str | None = None,
        timeout: int | None = 120,
    ) -> CommandResult:
        target = f"{self.user}@{self.host}" if self.user else self.host
        remote = shlex.join(argv)
        command = [
            "ssh",
            "-o",
            "BatchMode=no" if self.password else "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-p",
            str(self.port),
            target,
            remote,
        ]
        env = None
        if self.password:
            command = ["sshpass", "-e", *command]
            env = dict(os.environ, SSHPASS=self.password)
        result = subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        output = CommandResult(list(argv), result.returncode, result.stdout, result.stderr)
        if check and not output.ok:
            raise ProxyError(
                f"falló {' '.join(argv)} en {self.host}: {output.text().strip()}",
                EXIT_TRANSPORT,
            )
        return output


def build_runner(settings: Settings) -> Runner:
    if settings.is_remote:
        return SshRunner(
            settings.ssh_host or "",
            settings.ssh_user,
            settings.ssh_port,
            settings.ssh_password,
        )
    return LocalRunner()


# ---- certificados ---------------------------------------------------------


@dataclass
class CertificateInfo:
    state: str
    issuer: str | None = None
    subject: str | None = None
    not_after: str | None = None
    sans: tuple[str, ...] = ()
    verified: bool = False
    error: str | None = None


def probe_certificate(
    servername: str,
    host: str,
    port: int,
    ca_file: Path | None,
    timeout: float = 5.0,
) -> CertificateInfo:
    """Inspecciona el certificado servido para `servername`.

    Devuelve estado `active` solo si la cadena valida contra la CA configurada.
    """
    try:
        address = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return CertificateInfo(state="pending_dns", error=str(exc))

    family, socktype, proto, _, sockaddr = address[0]
    context = ssl.create_default_context()
    if ca_file:
        context.load_verify_locations(cafile=str(ca_file))
    verified = True
    cert_dict: dict = {}
    der: bytes | None = None
    try:
        with socket.socket(family, socktype, proto) as raw:
            raw.settimeout(timeout)
            raw.connect(sockaddr)
            with context.wrap_socket(raw, server_hostname=servername) as tls:
                der = tls.getpeercert(binary_form=True)
                cert_dict = tls.getpeercert()
    except ssl.SSLCertVerificationError as exc:
        verified = False
        error = str(exc)
        insecure = ssl.create_default_context()
        insecure.check_hostname = False
        insecure.verify_mode = ssl.CERT_NONE
        try:
            with socket.socket(family, socktype, proto) as raw:
                raw.settimeout(timeout)
                raw.connect(sockaddr)
                with insecure.wrap_socket(raw, server_hostname=servername) as tls:
                    der = tls.getpeercert(binary_form=True)
        except OSError as retry_exc:  # pragma: no cover - defensivo
            return CertificateInfo(state="pending_cert", error=retry_exc)
        return _describe(der, verified=False, error=error)
    except (TimeoutError, ConnectionError, OSError) as exc:
        return CertificateInfo(state="pending_cert", error=str(exc))

    info = _describe(der, verified=verified)
    if not info.issuer and cert_dict:
        issuer = cert_dict.get("issuer") or ()
        info.issuer = _flatten_name(issuer)
        info.subject = _flatten_name(cert_dict.get("subject") or ())
        info.not_after = cert_dict.get("notAfter")
        info.sans = tuple(value for _, value in cert_dict.get("subjectAltName", ()))
    return info


def _describe(der: bytes | None, verified: bool, error: str | None = None) -> CertificateInfo:
    state = "active" if verified else "pending_cert"
    if not der:
        return CertificateInfo(state=state, verified=False, error=error)
    try:
        from cryptography import x509
    except ImportError:  # pragma: no cover - cryptography está disponible en el proyecto
        return CertificateInfo(state=state, verified=verified, error=error)

    cert = x509.load_der_x509_certificate(der)
    try:
        not_after = cert.not_valid_after_utc.isoformat()
    except AttributeError:  # pragma: no cover
        not_after = cert.not_valid_after.isoformat()
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        sans = tuple(san.value.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        sans = ()
    return CertificateInfo(
        state=state,
        issuer=cert.issuer.rfc4514_string(),
        subject=cert.subject.rfc4514_string(),
        not_after=not_after,
        sans=sans,
        verified=verified,
        error=error,
    )


def _flatten_name(name: Iterable) -> str:
    parts = []
    for rdn in name:
        for key, value in rdn:
            parts.append(f"{key}={value}")
    return ", ".join(parts)
