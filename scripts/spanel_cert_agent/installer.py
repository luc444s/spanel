"""Instalación del par en rutas estables por hostname."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .config import AgentConfig
from .validation import CertificateInfo, Runner, validate_pair


class InstallError(Exception):
    pass


def install_paths(config: AgentConfig, hostname: str) -> tuple[Path, Path]:
    directory = config.install_root / hostname
    return directory / "fullchain.pem", directory / "privkey.pem"


def _atomic_copy(source: Path, destination: Path, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as target, source.open("rb") as origin:
            shutil.copyfileobj(origin, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
        os.chmod(destination, mode)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def install_pair(
    config: AgentConfig,
    hostname: str,
    lineage: Path,
    *,
    now=None,
    runner: Runner | None = None,
) -> tuple[Path, Path, CertificateInfo]:
    """Valida y copia; nunca instala si clave, SAN o fechas fallan (I7)."""

    cert_source = lineage / "fullchain.pem"
    key_source = lineage / "privkey.pem"
    if not cert_source.is_file() or not key_source.is_file():
        raise InstallError(f"El linaje de Certbot está incompleto: {lineage}")

    kwargs = {}
    if now is not None:
        kwargs["now"] = now
    if runner is not None:
        kwargs["runner"] = runner
    info = validate_pair(
        cert_source,
        key_source,
        hostname,
        ca_file=config.certbot.ca_file,
        **kwargs,
    )

    cert_path, key_path = install_paths(config, hostname)
    _atomic_copy(cert_source, cert_path, 0o644)
    _atomic_copy(key_source, key_path, 0o600)
    return cert_path, key_path, info
