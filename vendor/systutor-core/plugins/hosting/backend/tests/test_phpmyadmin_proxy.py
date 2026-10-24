from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

from plugins.hosting.backend.phpmyadmin_proxy import PmaClient, proxy_response
from systutor.core.errors import AppError


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        if self.path.startswith("/js/dist/cross_framing_protection.js"):
            body = b"top.location = self.location;"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
        elif self.path.startswith("/assets/"):
            body = b"\x00\x01\x02"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
        elif self.headers.get("Cookie") == "pma=valid":
            body = (
                b'<style id="cfs-style">html{display: none;}</style>'
                b'<script src="js/dist/cross_framing_protection.js?v=5.2.3"></script>'
                b'<a href="http://127.0.0.1:8081/index.php">Dashboard wp_test</a>'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("X-Frame-Options", "DENY")
        else:
            body = (
                b'<form><input name="token" value="csrf">'
                b'<input name="set_session" value="abc">'
                b'<input name="pma_username"><input name="pma_password"></form>'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", "pma=initial")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/index.php" and parse_qs(body.decode()).get("pma_password") == ["secret"]:
            self.send_response(302)
            self.send_header("Set-Cookie", "pma=valid; HttpOnly")
            self.send_header("Location", "/index.php")
        else:
            self.send_response(403)
        self.end_headers()


@pytest.fixture
def client():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield PmaClient(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_server_side_login_and_proxy(client: PmaClient) -> None:
    cookies = client.login("wp_test", "secret")
    assert cookies == {"pma": "valid"}
    response, updated, expired = proxy_response(
        client, "GET", "", "", b"", {}, cookies, "", "/proxy"
    )
    assert not expired and updated == cookies
    assert b"/proxy/index.php" in response.body
    assert b"cfs-style" not in response.body
    assert b"cross_framing_protection.js" not in response.body
    assert "x-frame-options" not in response.headers
    assert "set-cookie" not in response.headers
    guard, _, _ = proxy_response(
        client, "GET", "js/dist/cross_framing_protection.js", "v=5.2.3",
        b"", {}, cookies, "", "/proxy",
    )
    assert b"top.location" not in guard.body
    assert b"framed through SPanel" in guard.body
    _, upstream = client.request("GET", "assets/a")
    assert upstream.read() == b"\x00\x01\x02"


def test_rejected_login_and_expired_cookie(client: PmaClient) -> None:
    with pytest.raises(AppError) as error:
        client.login("wp_test", "wrong")
    assert error.value.status_code == 502
    assert "wrong" not in error.value.message
    response, _, expired = proxy_response(
        client, "GET", "", "", b"", {}, {"pma": "expired"}, "", "/proxy"
    )
    assert expired and response.status_code == 401
