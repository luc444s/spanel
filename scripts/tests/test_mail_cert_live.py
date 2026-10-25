#!/usr/bin/env python3
"""Prueba live del correo interno con TLS validado por la CA local (A.SPEC 0028).

Verifica, contra un servidor real, que:

1. IMAPS `993` presenta un certificado emitido por el CA del laboratorio para el
   hostname de cliente, y que la cadena valida con `root_ca.crt`.
2. SMTPS `465` y submission `587` (STARTTLS) presentan el mismo certificado.
3. Un mensaje enviado por submission se recupera por IMAPS, con verificación de
   certificado activa en las dos conexiones (nunca `CERT_NONE` ni
   `check_hostname = False`).

Uso:

    python3 scripts/tests/test_mail_cert_live.py          # standalone, sin pytest
    python3 -m pytest scripts/tests/test_mail_cert_live.py -q -s

Se salta solo si falta la contraseña del buzón de prueba. Configuración por
entorno:

    SPANEL_MAIL_DOMAIN         default mail.lucas.spanel
    SPANEL_MAIL_HOST           default = SPANEL_MAIL_DOMAIN
    MAIL_TEST_USER             default lucas@lucas.spanel
    MAIL_TEST_PASSWORD_FILE    default /home/lucas/.spanel-mail-test
    MAIL_TEST_PASSWORD         si está definida, gana sobre el archivo
    SPANEL_CA_ROOT             default scripts/traefik-lab/ca/root_ca.crt
    SPANEL_MAIL_TIMEOUT        default 30 (segundos por operación de red)
"""

from __future__ import annotations

import imaplib
import os
import smtplib
import ssl
import sys
import time
import traceback
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

try:  # pytest opcional: en el host del laboratorio no está instalado
    import pytest

    Skipped = pytest.skip.Exception
except ImportError:  # pragma: no cover - camino standalone
    pytest = None

    class Skipped(Exception):
        """Se lanza cuando falta configuración para la prueba live."""


LAB_DIR = Path(__file__).resolve().parents[1] / "traefik-lab"

DOMAIN = os.getenv("SPANEL_MAIL_DOMAIN", "mail.lucas.spanel")
HOST = os.getenv("SPANEL_MAIL_HOST") or DOMAIN
USER = os.getenv("MAIL_TEST_USER", "lucas@lucas.spanel")
PASSWORD_FILE = Path(os.getenv("MAIL_TEST_PASSWORD_FILE", "/home/lucas/.spanel-mail-test"))
CA_ROOT = Path(os.getenv("SPANEL_CA_ROOT", str(LAB_DIR / "ca" / "root_ca.crt")))
TIMEOUT = int(os.getenv("SPANEL_MAIL_TIMEOUT", "30"))
ISSUER_EXPECTED = "Spanel Lab CA"
EVIDENCE: list[str] = []


def _password() -> str:
    explicit = os.getenv("MAIL_TEST_PASSWORD")
    if explicit:
        return explicit
    if PASSWORD_FILE.exists():
        return PASSWORD_FILE.read_text().strip()
    raise Skipped(
        f"falta la contraseña del buzón: definí MAIL_TEST_PASSWORD o {PASSWORD_FILE}"
    )


def _require_lab() -> None:
    """Sin credenciales del buzón no es un entorno de laboratorio: se omite.

    Evita que un `pytest` genérico en una máquina sin el laboratorio intente
    conectarse a un nombre interno y falle por red.
    """
    if os.getenv("MAIL_TEST_PASSWORD") or PASSWORD_FILE.exists():
        return
    raise Skipped(
        f"sin credenciales del buzón ({PASSWORD_FILE}); prueba live omitida"
    )


def _context() -> ssl.SSLContext:
    if not CA_ROOT.exists():
        raise Skipped(f"falta la CA local: {CA_ROOT}")
    # create_default_context con cafile NO desactiva la verificación: es el punto
    # del test. Si alguien lo cambia por CERT_NONE, este archivo se nota.
    context = ssl.create_default_context(cafile=str(CA_ROOT))
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    return context


def _peer(cert: dict) -> tuple[str, str, list[tuple[str, str]]]:
    issuer = dict(item[0] for item in cert.get("issuer", ()))
    subject = dict(item[0] for item in cert.get("subject", ()))
    return (
        issuer.get("organizationName", ""),
        subject.get("commonName", ""),
        list(cert.get("subjectAltName", ())),
    )


def _assert_certificate(cert: dict, label: str) -> None:
    issuer, subject, sans = _peer(cert)
    names = [value for kind, value in sans if kind == "DNS"]
    assert ISSUER_EXPECTED in issuer, f"{label}: emisor inesperado {issuer!r}"
    assert subject == DOMAIN, f"{label}: CN inesperado {subject!r}"
    assert DOMAIN in names, f"{label}: el SAN no incluye {DOMAIN}: {names}"
    assert cert.get("notAfter"), f"{label}: el certificado no tiene notAfter"
    serial = cert.get("serialNumber", "")
    EVIDENCE.append(f"{label}: serial={serial} no_after={cert['notAfter']} san={names}")


def test_imaps_993_presenta_el_certificado_de_la_ca_local() -> None:
    _require_lab()
    context = _context()
    client = imaplib.IMAP4_SSL(HOST, 993, ssl_context=context)
    try:
        _assert_certificate(client.sock.getpeercert(), "imaps:993")
    finally:
        try:
            client.shutdown()
        except Exception:
            pass


def test_smtps_465_presenta_el_certificado_de_la_ca_local() -> None:
    _require_lab()
    context = _context()
    client = smtplib.SMTP_SSL(HOST, 465, timeout=TIMEOUT, context=context)
    try:
        _assert_certificate(client.sock.getpeercert(), "smtps:465")
    finally:
        try:
            client.quit()
        except Exception:
            pass


def test_submission_587_starttls_presenta_el_certificado_de_la_ca_local() -> None:
    _require_lab()
    context = _context()
    client = smtplib.SMTP(HOST, 587, timeout=TIMEOUT)
    try:
        client.ehlo()
        code, _ = client.starttls(context=context)
        assert code == 220, f"starttls: código {code}"
        client.ehlo()
        _assert_certificate(client.sock.getpeercert(), "submission:587")
    finally:
        try:
            client.quit()
        except Exception:
            pass


def test_envio_y_lectura_interna_con_tls() -> None:
    _require_lab()
    password = _password()
    context = _context()
    message_id = make_msgid(domain=DOMAIN)

    message = EmailMessage()
    message["From"] = USER
    message["To"] = USER
    message["Subject"] = "prueba interna mail.lucas.spanel"
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = message_id
    message.set_content(
        "Correo interno de prueba: TLS validado contra la CA local, sin relay.\n"
    )

    with smtplib.SMTP(HOST, 587, timeout=TIMEOUT) as client:
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
        client.login(USER, password)
        refused = client.send_message(message)
        assert not refused, f"destinatarios rechazados: {refused}"

    deadline = time.time() + TIMEOUT
    found: list[bytes] = []
    headers = ""
    while time.time() < deadline and not found:
        client = imaplib.IMAP4_SSL(HOST, 993, ssl_context=context)
        try:
            client.login(USER, password)
            _assert_certificate(client.sock.getpeercert(), "imaps:993")
            client.select("INBOX")
            typ, data = client.search(None, "HEADER", "Message-ID", message_id)
            assert typ == "OK", f"search: {typ}"
            found = data[0].split()
            if found:
                typ, data = client.fetch(
                    found[0], "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT MESSAGE-ID RECEIVED)])"
                )
                headers = data[0][1].decode("utf-8", "replace")
        finally:
            try:
                client.logout()
            except Exception:
                pass
        if not found:
            time.sleep(1)

    assert found, f"el mensaje {message_id} no apareció en INBOX en {TIMEOUT}s"
    assert message_id in headers, "el Message-ID recuperado no coincide"
    assert USER in headers, "las cabeceras recuperadas no muestran el destinatario"
    assert "LMTP" in headers or "Postfix" in headers, (
        "las cabeceras no muestran entrega local: " + headers
    )


def _presented(port: int) -> dict:
    context = _context()
    if port == 993:
        client = imaplib.IMAP4_SSL(HOST, port, ssl_context=context)
        try:
            return client.sock.getpeercert()
        finally:
            try:
                client.shutdown()
            except Exception:
                pass
    if port == 465:
        client = smtplib.SMTP_SSL(HOST, port, timeout=TIMEOUT, context=context)
        try:
            return client.sock.getpeercert()
        finally:
            try:
                client.quit()
            except Exception:
                pass
    client = smtplib.SMTP(HOST, port, timeout=TIMEOUT)
    try:
        client.ehlo()
        client.starttls(context=context)
        return client.sock.getpeercert()
    finally:
        try:
            client.quit()
        except Exception:
            pass


def test_los_tres_puertos_presentan_el_mismo_certificado() -> None:
    _require_lab()
    serials = {port: _presented(port).get("serialNumber") for port in (993, 465, 587)}
    assert len(set(serials.values())) == 1, f"certificados distintos por puerto: {serials}"


TESTS = (
    test_imaps_993_presenta_el_certificado_de_la_ca_local,
    test_los_tres_puertos_presentan_el_mismo_certificado,
    test_smtps_465_presenta_el_certificado_de_la_ca_local,
    test_submission_587_starttls_presenta_el_certificado_de_la_ca_local,
    test_envio_y_lectura_interna_con_tls,
)


def main() -> int:
    print(f"objetivo: {USER} en {HOST} (SNI {DOMAIN})")
    print(f"CA:       {CA_ROOT}")
    failures = 0
    skipped = 0
    for test in TESTS:
        name = test.__name__
        try:
            test()
        except Skipped as exc:
            skipped += 1
            print(f"SKIP  {name}: {exc}")
        except AssertionError as exc:
            failures += 1
            print(f"FALLA {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - se reporta el error real
            failures += 1
            print(f"FALLA {name}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
        else:
            print(f"PASS  {name}")
            for line in EVIDENCE:
                print(f"      {line}")
            EVIDENCE.clear()
    print(
        f"\n{len(TESTS) - failures - skipped} ok, {failures} fallas, {skipped} omitidos"
    )
    if skipped == len(TESTS):
        print("resultado: SKIP (falta configuración)")
        return 2
    print("resultado:", "OK" if failures == 0 else "FALLA")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
