"""Validación del agente: payload, allowlist y material del certificado.

Las comprobaciones usan `openssl` con argv fijo y sin shell. La misma lógica de
clave/certificado, SAN y fechas que documenta `scripts/mail_cert.py audit`.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOSTNAME_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"
)
REQUEST_KEYS = frozenset({"id", "hostname", "action", "requested_by", "requested_at", "reason"})
REFRESH_KEYS = frozenset({"id", "action", "requested_by", "requested_at", "reason"})
SECRET_MARKERS = ("PRIVATE KEY",)

Runner = Callable[[Sequence[str], bytes | None], subprocess.CompletedProcess]


class CertificateValidationError(Exception):
    """El certificado o la solicitud no son seguros para instalar."""


@dataclass(slots=True)
class CertificateInfo:
    subject: str | None = None
    issuer: str | None = None
    serial: str | None = None
    fingerprint: str | None = None
    not_before: datetime | None = None
    not_after: datetime | None = None
    sans: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "issuer": self.issuer,
            "serial": self.serial,
            "fingerprint": self.fingerprint,
            "not_before": self.not_before.isoformat(timespec="seconds") if self.not_before else None,
            "not_after": self.not_after.isoformat(timespec="seconds") if self.not_after else None,
            "sans": list(self.sans),
        }


def normalize_hostname(value: object) -> str:
    candidate = str(value or "").strip().lower().rstrip(".")
    if not candidate or len(candidate) > 253 or not HOSTNAME_RE.match(candidate):
        raise CertificateValidationError("Hostname inválido")
    return candidate


def validate_request_payload(payload: object, *, default_name: str) -> dict:
    """Rechaza campos extra y valores fuera del contrato de solicitud."""

    if not isinstance(payload, dict):
        raise CertificateValidationError("La solicitud debe ser un objeto JSON")
    action = str(payload.get("action", "")).strip().lower()
    if action == "refresh":
        unknown = set(payload) - set(REFRESH_KEYS)
        if unknown:
            raise CertificateValidationError(f"Campos no permitidos: {sorted(unknown)}")
        return {"action": "refresh", "id": payload.get("id")}

    unknown = set(payload) - set(REQUEST_KEYS)
    if unknown:
        raise CertificateValidationError(f"Campos no permitidos: {sorted(unknown)}")
    hostname = normalize_hostname(payload.get("hostname"))
    if hostname == default_name:
        raise CertificateValidationError("El certificado global no se regenera")
    if action not in ("ensure", "force", "verify", "sync", "install", "rollback"):
        raise CertificateValidationError("Acción inválida")
    reason = payload.get("reason")
    if reason is not None:
        if not isinstance(reason, str) or len(reason) > 200:
            raise CertificateValidationError("Motivo inválido")
        if any(ord(char) < 32 for char in reason):
            raise CertificateValidationError("Motivo inválido")
    return {
        "id": payload.get("id"),
        "hostname": hostname,
        "action": action,
        "requested_by": payload.get("requested_by"),
        "requested_at": payload.get("requested_at"),
        "reason": reason,
    }


def contains_secret_material(text: str) -> bool:
    upper = text.upper()
    return any(marker in upper for marker in SECRET_MARKERS)


def safe_error(value: object, limit: int = 500) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    if contains_secret_material(value):
        return "Detalle no disponible"
    return value.strip()[:limit]


def _run(args: Sequence[str], input_data: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args),
        input=input_data,
        capture_output=True,
        check=False,
    )


def _openssl(args: Sequence[str], *, runner: Runner = _run) -> str:
    process = runner(["openssl", *args], None)
    if process.returncode != 0:
        detail = (process.stderr or process.stdout or b"").decode("utf-8", "replace").strip()
        raise CertificateValidationError(f"openssl falló: {detail[:200]}")
    return process.stdout.decode("utf-8", "replace")


def _normalized_pem(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def public_key_matches(cert: Path, key: Path, *, runner: Runner = _run) -> bool:
    try:
        cert_pub = _openssl(["x509", "-in", str(cert), "-noout", "-pubkey"], runner=runner)
        key_pub = _openssl(["pkey", "-in", str(key), "-pubout"], runner=runner)
    except CertificateValidationError:
        return False
    return _normalized_pem(cert_pub) == _normalized_pem(key_pub)


def _parse_date(value: str) -> datetime:
    text = value.split("=", 1)[1].strip() if "=" in value else value.strip()
    for fmt in ("%b %d %H:%M:%S %Y %Z", "%b %d %H:%M:%S %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise CertificateValidationError(f"Fecha inválida: {text}")


def inspect_certificate(cert: Path, *, runner: Runner = _run) -> CertificateInfo:
    subject = _openssl(["x509", "-in", str(cert), "-noout", "-subject"], runner=runner).strip()
    issuer = _openssl(["x509", "-in", str(cert), "-noout", "-issuer"], runner=runner).strip()
    serial = _openssl(["x509", "-in", str(cert), "-noout", "-serial"], runner=runner).strip()
    fingerprint = _openssl(
        ["x509", "-in", str(cert), "-noout", "-fingerprint", "-sha256"], runner=runner
    ).strip()
    dates = _openssl(
        ["x509", "-in", str(cert), "-noout", "-startdate", "-enddate"], runner=runner
    ).splitlines()
    if len(dates) < 2:
        raise CertificateValidationError("No se pudieron leer las fechas del certificado")
    san_text = _openssl(
        ["x509", "-in", str(cert), "-noout", "-ext", "subjectAltName"], runner=runner
    )
    sans = sorted(
        {
            part.strip().lower()
            for line in san_text.splitlines()[1:]
            for part in line.split(",")
            if part.strip().lower().startswith("dns:")
            for part in [part.split(":", 1)[1]]
        }
    )
    return CertificateInfo(
        subject=subject.split("=", 1)[1] if "=" in subject else subject,
        issuer=issuer.split("=", 1)[1] if "=" in issuer else issuer,
        serial=serial.split("=", 1)[1] if "=" in serial else serial,
        fingerprint=fingerprint.split("=", 1)[1] if "=" in fingerprint else fingerprint,
        not_before=_parse_date(dates[0]),
        not_after=_parse_date(dates[1]),
        sans=sans,
    )


def _hostname_in_sans(hostname: str, sans: list[str]) -> bool:
    if hostname in sans:
        return True
    for san in sans:
        if not san.startswith("*."):
            continue
        suffix = san[1:]  # ".example.com"
        if hostname.endswith(suffix) and hostname.count(".") == san.count("."):
            return True
    return False


def validate_pair(
    cert: Path,
    key: Path,
    hostname: str,
    *,
    ca_file: Path | None = None,
    now: datetime | None = None,
    runner: Runner = _run,
) -> CertificateInfo:
    """Clave ↔ certificado, SAN y fechas; nada se instala si falla (I7)."""

    if not cert.is_file():
        raise CertificateValidationError(f"Falta el certificado: {cert}")
    if not key.is_file():
        raise CertificateValidationError(f"Falta la clave: {key}")
    if not public_key_matches(cert, key, runner=runner):
        raise CertificateValidationError("La clave no corresponde al certificado")

    info = inspect_certificate(cert, runner=runner)
    if not _hostname_in_sans(hostname, info.sans):
        raise CertificateValidationError(f"El SAN no contiene {hostname}")
    reference = now or datetime.now(timezone.utc)
    if info.not_after is None or info.not_after <= reference:
        raise CertificateValidationError("El certificado está vencido")
    if info.not_before is not None and info.not_before > reference + timedelta(minutes=5):
        raise CertificateValidationError("El certificado todavía no es válido")

    if ca_file is not None:
        if not ca_file.is_file():
            raise CertificateValidationError(f"Falta el bundle de CA: {ca_file}")
        args = ["verify", "-CAfile", str(ca_file), str(cert)]
        process = runner(["openssl", *args], None)
        if process.returncode != 0:
            detail = (process.stdout or process.stderr or b"").decode("utf-8", "replace")
            raise CertificateValidationError(f"La cadena no valida: {detail.strip()[:200]}")
    return info
