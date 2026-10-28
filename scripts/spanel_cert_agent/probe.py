"""Probes TLS de solo lectura para 993/465/587.

Cada probe abre una conexión nueva, valida la cadena con la CA configurada y
compara el serial servido con el inventario. Nunca escribe archivos ni recarga
servicios (I1). El resultado solo contiene metadatos: serial, huella, SAN y
fechas; jamás la clave ni el PEM.
"""

from __future__ import annotations

import hashlib
import socket
import ssl
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

Connector = Callable[[str, int, str | None, ssl.SSLContext, float], dict]

SNI_PORTS = (993, 465, 587)
STARTTLS_PORTS = frozenset({25, 587})


class ProbeError(Exception):
    """El handshake o la identidad del certificado servido no son válidos."""


@dataclass(slots=True)
class ProbeResult:
    port: int
    ok: bool
    serial: str | None = None
    fingerprint: str | None = None
    not_after: str | None = None
    sans: list[str] = field(default_factory=list)
    error: str | None = None
    duration_ms: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "port": self.port,
            "ok": self.ok,
            "serial": self.serial,
            "fingerprint": self.fingerprint,
            "not_after": self.not_after,
            "sans": list(self.sans),
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


def _peer_metadata(tls: ssl.SSLSocket) -> dict:
    """Extrae metadatos del peer sin conservar material del certificado."""

    cert = tls.getpeercert() or {}
    der = tls.getpeercert(binary_form=True) or b""
    sans: list[str] = []
    for kind, value in cert.get("subjectAltName", ()):  # type: ignore[union-attr]
        if kind == "DNS" and value:
            sans.append(str(value).strip().lower())
    not_after_raw = cert.get("notAfter")
    not_after = None
    if isinstance(not_after_raw, str):
        try:
            parsed = datetime.strptime(  # noqa: DTZ007 - se normaliza a UTC abajo
                not_after_raw, "%b %d %H:%M:%S %Y %Z"
            )
            not_after = parsed.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            not_after = not_after_raw
    return {
        "serial": str(cert.get("serialNumber") or "") or None,
        "fingerprint": hashlib.sha256(der).hexdigest() if der else None,
        "not_after": not_after,
        "sans": sorted(set(sans)),
    }


def _context(ca_file: Path | None, *, verify_identity: bool) -> ssl.SSLContext:
    if ca_file is None:
        context = ssl.create_default_context()
    else:
        context = ssl.create_default_context(cafile=str(ca_file))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = verify_identity
    if verify_identity:
        context.verify_mode = ssl.CERT_REQUIRED
    return context


def _direct(host: str, port: int, servername: str | None, context: ssl.SSLContext, timeout: float) -> dict:
    with socket.create_connection((host, port), timeout=timeout) as raw:
        raw.settimeout(timeout)
        with context.wrap_socket(raw, server_hostname=servername) as tls:
            return _peer_metadata(tls)


def _starttls(
    host: str, port: int, servername: str | None, context: ssl.SSLContext, timeout: float
) -> dict:
    with socket.create_connection((host, port), timeout=timeout) as raw:
        raw.settimeout(timeout)
        banner = _read_smtp_reply(raw)
        if not banner.startswith(b"220"):
            raise ProbeError("El servidor SMTP no respondió 220")
        raw.sendall(b"EHLO spanel-cert-agent\r\n")
        _read_smtp_reply(raw)
        raw.sendall(b"STARTTLS\r\n")
        reply = _read_smtp_reply(raw)
        if not reply.startswith(b"220"):
            raise ProbeError("El servidor SMTP rechazó STARTTLS")
        with context.wrap_socket(raw, server_hostname=servername) as tls:
            return _peer_metadata(tls)


def _read_smtp_reply(raw: socket.socket) -> bytes:
    chunks: list[bytes] = []
    while True:
        line = raw.recv(4096)
        if not line:
            break
        chunks.append(line)
        # Una respuesta multilínea termina con "NNN " (espacio, no guion).
        if len(line) >= 4 and line[3:4] == b" ":
            break
    return b"".join(chunks)


def default_connector(
    host: str, port: int, servername: str | None, context: ssl.SSLContext, timeout: float
) -> dict:
    if port in STARTTLS_PORTS:
        return _starttls(host, port, servername, context, timeout)
    return _direct(host, port, servername, context, timeout)


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def probe(
    host: str,
    port: int,
    *,
    servername: str | None,
    ca_file: Path | None,
    timeout: float = 10.0,
    expected_serial: str | None = None,
    connector: Connector | None = None,
    verify_identity: bool | None = None,
) -> ProbeResult:
    """Un probe TLS.

    `servername=None` desactiva SNI y la validación de hostname: sirve para
    comprobar que el certificado predeterminado sigue siendo `cf-mail`. En ese
    caso la identidad se prueba comparando el serial con el esperado.
    """

    identity = verify_identity if verify_identity is not None else servername is not None
    start = time.monotonic()
    try:
        context = _context(ca_file if connector is None else None, verify_identity=identity)
        call = connector or default_connector
        metadata = call(host, port, servername, context, timeout)
    except (OSError, ssl.SSLError, ProbeError) as exc:
        return ProbeResult(
            port=port,
            ok=False,
            error=str(exc)[:300] or "Fallo TLS",
            duration_ms=_elapsed_ms(start),
        )
    serial = metadata.get("serial")
    if expected_serial and serial != expected_serial:
        return ProbeResult(
            port=port,
            ok=False,
            serial=serial,
            fingerprint=metadata.get("fingerprint"),
            not_after=metadata.get("not_after"),
            sans=list(metadata.get("sans") or []),
            error=f"Serial servido distinto al inventario ({serial or 'sin serial'})",
            duration_ms=_elapsed_ms(start),
        )
    return ProbeResult(
        port=port,
        ok=True,
        serial=serial,
        fingerprint=metadata.get("fingerprint"),
        not_after=metadata.get("not_after"),
        sans=list(metadata.get("sans") or []),
        duration_ms=_elapsed_ms(start),
    )


def verify_hostname(
    hostname: str,
    *,
    expected_serial: str | None,
    ca_file: Path | None,
    ports: Sequence[int] = SNI_PORTS,
    timeout: float = 10.0,
    no_sni_expected_serial: str | None = None,
    connector: Connector | None = None,
    host: str = "127.0.0.1",
) -> dict[str, object]:
    """Probes con SNI y sin SNI; devuelve solo booleanos y metadatos."""

    sni: dict[str, bool] = {}
    details: dict[str, object] = {}
    for port in ports:
        result = probe(
            host,
            port,
            servername=hostname,
            ca_file=ca_file,
            timeout=timeout,
            expected_serial=expected_serial,
            connector=connector,
        )
        sni[str(port)] = result.ok
        details[str(port)] = result.as_dict()

    no_sni: dict[str, bool] = {}
    no_sni_details: dict[str, object] = {}
    for port in ports:
        result = probe(
            host,
            port,
            servername=None,
            ca_file=ca_file,
            timeout=timeout,
            expected_serial=no_sni_expected_serial,
            connector=connector,
        )
        no_sni[str(port)] = result.ok
        no_sni_details[str(port)] = result.as_dict()

    return {
        "sni": sni,
        "no_sni": no_sni,
        "no_sni_ok": all(no_sni.values()) if no_sni else True,
        "sni_ok": all(sni.values()) if sni else True,
        "served_serial": (details.get(str(ports[0])) or {}).get("serial"),
        "no_sni_serial": (no_sni_details.get(str(ports[0])) or {}).get("serial"),
        "details": details,
        "no_sni_details": no_sni_details,
    }
