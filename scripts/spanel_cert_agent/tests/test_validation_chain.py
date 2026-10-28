"""Validación de cadena: los intermedios del fullchain deben usarse."""

from __future__ import annotations

from pathlib import Path

from spanel_cert_agent.validation import chain_verify_command


def test_verify_usa_el_fullchain_como_untrusted(tmp_path: Path) -> None:
    cert = tmp_path / "fullchain.pem"
    ca = tmp_path / "ca.pem"

    command = chain_verify_command(cert, ca)

    assert command[0] == "verify"
    assert command[1:3] == ["-CAfile", str(ca)]
    assert "-untrusted" in command
    assert command[command.index("-untrusted") + 1] == str(cert)
    assert command[-1] == str(cert)
