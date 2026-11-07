#!/usr/bin/env python3
"""Sirve una vez un ZIP de A.SPEC 0047 mediante un enlace temporal.

El token tiene 256 bits, se conserva solo como SHA256 en memoria y se consume
atómicamente antes del primer byte del GET. HEAD no consume. El proceso termina
después de esa transferencia (incluso si el cliente la corta) o cuando vence el
TTL. Es un prototipo operativo stdlib-only; no reemplaza el futuro endpoint HTTPS
del panel.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import secrets
import ssl
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote, urlsplit

from site_backup import VerifyError, verify_zip

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_EXPIRED = 2
CHUNK_SIZE = 1024 * 1024
DEFAULT_TTL = 300
MAX_TTL = 3600


class LinkError(Exception):
    """Error de configuración o de validación previo a escuchar."""


@dataclass
class DownloadState:
    """Estado compartido por handlers; claim() es el punto de consumo atómico."""

    zip_path: Path
    token_digest: bytes
    expires_at: float
    now: Callable[[], float] = time.monotonic
    lock: threading.Lock = field(default_factory=threading.Lock)
    done: threading.Event = field(default_factory=threading.Event)
    claimed: bool = False
    completed: bool = False

    @staticmethod
    def digest(token: str) -> bytes:
        return hashlib.sha256(token.encode("ascii", errors="strict")).digest()

    def valid_token(self, token: str) -> bool:
        try:
            candidate = self.digest(token)
        except (UnicodeError, ValueError):
            return False
        return hmac.compare_digest(candidate, self.token_digest)

    def status(self, token: str) -> str:
        """Return missing|expired|consumed|active without consuming."""
        if not self.valid_token(token):
            return "missing"
        with self.lock:
            if self.claimed:
                return "consumed"
            if self.now() >= self.expires_at:
                return "expired"
            return "active"

    def claim(self, token: str) -> str:
        """Atomically consume a valid live token before response headers."""
        if not self.valid_token(token):
            return "missing"
        with self.lock:
            if self.claimed:
                return "consumed"
            if self.now() >= self.expires_at:
                return "expired"
            self.claimed = True
            return "active"

    def finish(self, completed: bool) -> None:
        with self.lock:
            self.completed = completed
        self.done.set()

    def expire(self) -> None:
        self.done.set()


class OneTimeServer(ThreadingHTTPServer):
    allow_reuse_address: ClassVar[bool] = True
    daemon_threads: ClassVar[bool] = False
    block_on_close: ClassVar[bool] = True

    def __init__(self, address: tuple[str, int], state: DownloadState):
        self.download_state = state
        super().__init__(address, OneTimeHandler)


class OneTimeHandler(BaseHTTPRequestHandler):
    server: OneTimeServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args) -> None:
        """No access log: the request path contains the bearer token."""

    def _token(self) -> str | None:
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment or not parsed.path.startswith("/download/"):
            return None
        token = parsed.path.removeprefix("/download/")
        if not token or "/" in token:
            return None
        return token

    def _send_error(self, status: str) -> None:
        if status == "missing":
            code, body = 404, b"No encontrado\n"
        else:
            code, body = 410, b"Enlace expirado o consumido\n"
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def _send_download_headers(self) -> None:
        path = self.server.download_state.zip_path
        filename = quote(path.name, safe="")
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{filename}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_HEAD(self) -> None:
        token = self._token()
        status = "missing" if token is None else self.server.download_state.status(token)
        if status != "active":
            self._send_error(status)
            return
        self._send_download_headers()

    def do_GET(self) -> None:
        token = self._token()
        status = "missing" if token is None else self.server.download_state.claim(token)
        if status != "active":
            self._send_error(status)
            return

        completed = False
        try:
            self._send_download_headers()
            with self.server.download_state.zip_path.open("rb") as source:
                for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                    self.wfile.write(chunk)
            completed = True
        except (BrokenPipeError, ConnectionResetError, OSError):
            # El token queda consumido aunque el cliente interrumpa: reintentar
            # con el mismo link rompería la garantía one-time.
            pass
        finally:
            self.server.download_state.finish(completed)


def _validated_zip(path_text: str) -> Path:
    try:
        path = Path(path_text).expanduser().resolve(strict=True)
    except OSError as exc:
        raise LinkError(f"no existe el ZIP: {path_text}") from exc
    if not path.is_file() or path.suffix.lower() != ".zip":
        raise LinkError(f"no es un archivo .zip: {path}")
    try:
        verify_zip(path)
    except (VerifyError, OSError, ValueError, TypeError) as exc:
        raise LinkError(f"ZIP inválido: {exc}") from exc
    return path


def _public_base(
    value: str | None,
    bind: str,
    port: int,
    *,
    tls_enabled: bool = False,
    allow_insecure_http: bool = False,
) -> str:
    expected_scheme = "https" if tls_enabled else "http"
    if value:
        parsed = urlsplit(value)
        if (
            parsed.scheme != expected_scheme
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise LinkError(
                f"--public-base-url debe ser un origen {expected_scheme}, sin path/query/credenciales"
            )
        if bind not in {"127.0.0.1", "localhost"} and not tls_enabled and not allow_insecure_http:
            raise LinkError(
                "HTTP público expondría el respaldo; usar --tls-cert/--tls-key "
                "o confirmar --allow-insecure-http"
            )
        return value.rstrip("/")
    if bind not in {"127.0.0.1", "localhost"}:
        raise LinkError("un --bind no-local exige --public-base-url explícito")
    return f"{expected_scheme}://{bind}:{port}"


def _enable_tls(server: OneTimeServer, cert: str | None, key: str | None) -> bool:
    if bool(cert) != bool(key):
        raise LinkError("--tls-cert y --tls-key deben declararse juntos")
    if not cert:
        return False
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert, keyfile=key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    except (OSError, ssl.SSLError) as exc:
        raise LinkError(f"no se pudo habilitar TLS: {exc}") from exc
    return True


def serve(server: OneTimeServer) -> bool:
    """Serve until first GET finishes or the unclaimed link expires."""
    state = server.download_state
    try:
        while not state.done.is_set():
            with state.lock:
                claimed = state.claimed
            if not claimed:
                remaining = state.expires_at - state.now()
                if remaining <= 0:
                    state.expire()
                    break
                server.timeout = min(0.25, remaining)
            else:
                # Keep accepting while the one valid transfer is active so a
                # concurrent second GET gets an explicit 410.
                server.timeout = 0.1
            server.handle_request()
    finally:
        server.server_close()
    return state.completed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Genera un enlace temporal de un uso para un ZIP válido de A.SPEC 0047."
    )
    parser.add_argument("--zip", required=True, dest="zip_path", help="respaldo .zip a servir")
    parser.add_argument("--bind", default="127.0.0.1", help="interfaz de escucha")
    parser.add_argument("--port", type=int, default=8765, help="puerto; 0 elige uno libre")
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL, help="vigencia en segundos")
    parser.add_argument(
        "--public-base-url",
        default=None,
        help="origen público anunciado, obligatorio si --bind no es local",
    )
    parser.add_argument("--tls-cert", default=None, help="certificado PEM para HTTPS")
    parser.add_argument("--tls-key", default=None, help="clave privada PEM para HTTPS")
    parser.add_argument(
        "--allow-insecure-http",
        action="store_true",
        help="acepta HTTP no-local conscientemente (no recomendado)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    server: OneTimeServer | None = None
    try:
        if not 0 <= args.port <= 65535:
            raise LinkError("--port debe estar entre 0 y 65535")
        if not 1 <= args.ttl <= MAX_TTL:
            raise LinkError(f"--ttl debe estar entre 1 y {MAX_TTL} segundos")
        zip_path = _validated_zip(args.zip_path)
        token = secrets.token_urlsafe(32)
        state = DownloadState(
            zip_path=zip_path,
            token_digest=DownloadState.digest(token),
            expires_at=time.monotonic() + args.ttl,
        )
        server = OneTimeServer((args.bind, args.port), state)
        actual_port = int(server.server_address[1])
        tls_enabled = _enable_tls(server, args.tls_cert, args.tls_key)
        base = _public_base(
            args.public_base_url,
            args.bind,
            actual_port,
            tls_enabled=tls_enabled,
            allow_insecure_http=args.allow_insecure_http,
        )
    except (LinkError, OSError) as exc:
        if server is not None:
            server.server_close()
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR

    link = f"{base}/download/{token}"
    print(f"LINK: {link}")  # única salida intencional que contiene el token
    print(f"EXPIRA_EN: {args.ttl}s")
    print(f"ARCHIVO: {zip_path.name} ({zip_path.stat().st_size} bytes)")
    sys.stdout.flush()
    completed = serve(server)
    if completed:
        print("DESCARGA: completada; enlace consumido")
        return EXIT_OK
    if state.claimed:
        print("DESCARGA: interrumpida; enlace consumido", file=sys.stderr)
        return EXIT_ERROR
    print("EXPIRADO: enlace no utilizado", file=sys.stderr)
    return EXIT_EXPIRED


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
