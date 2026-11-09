"""DB-only GitHub readiness reporting.

Readiness must never require decrypting a secret. The API reports an explicit
state so the UI can render one exclusive status instead of an error next to a
false "disconnected" label.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import GithubConnection
from .settings import AppsSettings

STATES = ("unconfigured", "pending_installation", "syncing", "connected", "suspended", "error")


def secret_available(path: Path) -> bool:
    """Return whether the secret file exists without reading its contents."""

    try:
        return path.is_file()
    except OSError:
        return False


def github_status(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    settings: AppsSettings,
) -> dict:
    """Legacy readiness projection used while OAuth and Apps coexist."""

    configured = bool(settings.public_url.strip()) and secret_available(
        settings.encryption_key_file
    )
    row = db.scalar(
        select(GithubConnection).where(GithubConnection.user_id == user_id)
    )
    if row is None:
        return {
            "state": "unconfigured" if configured else "error",
            "configured": configured,
            "connected": False,
            "reconnect_required": False,
            "login": None,
            "provider_count": 0,
            "installation_count": 0,
        }
    state = "connected" if not row.reconnect_required else "error"
    return {
        "state": state,
        "configured": configured,
        "connected": not row.reconnect_required,
        "reconnect_required": row.reconnect_required,
        "login": row.login,
        "github_user_id": row.github_user_id,
        "avatar_url": row.avatar_url,
        "scopes": row.scopes,
        "provider_count": 0,
        "installation_count": 0,
    }
