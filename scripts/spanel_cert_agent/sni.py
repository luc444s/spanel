"""Mapa SNI de Postfix y bloque `local_name` de Dovecot.

El agente genera ambos archivos desde el inventario instalado y ejecuta solo los
comandos de recarga configurados. Sin configuración falla cerrado antes de
escribir (I10).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from .config import AgentConfig


class SniError(Exception):
    pass


def render_postfix_map(entries: Mapping[str, tuple[Path, Path]]) -> str:
    lines = [
        f"{hostname} {cert} {key}"
        for hostname, (cert, key) in sorted(entries.items())
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def render_dovecot_local_name(entries: Mapping[str, tuple[Path, Path]]) -> str:
    blocks = [
        f"local_name {hostname} {{\n  ssl_cert = <{cert}\n  ssl_key = <{key}\n}}"
        for hostname, (cert, key) in sorted(entries.items())
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _atomic_text(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            shutil.copy2(path, path.with_name(f"{path.name}.spanel-bak"))
        except OSError:
            pass
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _run(command: Sequence[str], runner) -> None:
    process = runner(list(command), None)
    if process.returncode != 0:
        detail = (process.stderr or process.stdout or b"").decode("utf-8", "replace").strip()
        raise SniError(f"Falló {' '.join(command)}: {detail[:300]}")


def apply_sni(
    config: AgentConfig,
    entries: Mapping[str, tuple[Path, Path]],
    *,
    runner=subprocess.run,
) -> None:
    """Escribe mapas y recarga; cualquier falta de configuración aborta."""

    config.require_reload_config()
    postfix = config.postfix
    dovecot = config.dovecot
    assert postfix.sni_map_path is not None
    assert dovecot.local_name_path is not None

    _atomic_text(postfix.sni_map_path, render_postfix_map(entries))
    _atomic_text(dovecot.local_name_path, render_dovecot_local_name(entries))

    _run(postfix.postmap_command, runner)
    _run(postfix.reload_command, runner)
    _run(dovecot.reload_command, runner)
