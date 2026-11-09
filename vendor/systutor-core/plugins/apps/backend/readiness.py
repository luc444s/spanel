"""Readiness helpers that never decrypt or read secret material."""

from __future__ import annotations

from pathlib import Path


def secret_available(path: Path) -> bool:
    """Return whether the secret file exists without reading its contents."""

    try:
        return path.is_file()
    except OSError:
        return False
