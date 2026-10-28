"""STARTTLS: el banner SMTP puede llegar fragmentado."""

from __future__ import annotations

from spanel_cert_agent.probe import _read_smtp_reply


class FakeSocket:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)

    def recv(self, _size: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


def test_lee_banner_fragmentado_y_multilinea() -> None:
    socket = FakeSocket([b"220-mail.example ESMTP\r\n", b"220 mail.example\r\n"])

    assert _read_smtp_reply(socket) == b"220-mail.example ESMTP\r\n220 mail.example\r\n"


def test_lee_respuesta_de_una_linea() -> None:
    socket = FakeSocket([b"250 mail.example\r\n"])

    assert _read_smtp_reply(socket) == b"250 mail.example\r\n"
