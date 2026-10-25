"""Sondeo de certificados TLS sin desactivar la verificación."""

from __future__ import annotations

import socket
import ssl
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


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
