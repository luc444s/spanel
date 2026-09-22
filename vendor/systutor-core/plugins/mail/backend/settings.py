from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

# Load .env from project root (mailadmin/)
def _find_project_root() -> Path:
    current = Path(__file__).resolve().parent
    for parent in current.parents:
        if (parent / ".env").exists() or (parent / ".env.docker").exists():
            return parent
    return current.parents[min(5, len(current.parents) - 1)]

_PROJECT_ROOT = _find_project_root()
_ENV_FILE = _PROJECT_ROOT / ".env"


def _load_env() -> None:
    if not _ENV_FILE.exists():
        return
    for raw_line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        os.environ.setdefault(key, value.strip())


class MailSettings(BaseModel):
    mail_provider: str = "docker-mailserver"
    mail_server_host: str = ""
    mail_server_port: int = 22
    mail_server_user: str = "root"
    mail_server_password: str = ""
    mail_dms_container: str = "mailserver"
    mail_use_ssh: bool = False


_mail_settings: MailSettings | None = None


def register_mail_settings() -> None:
    global _mail_settings
    _load_env()
    _mail_settings = MailSettings(
        mail_provider=os.getenv("MAIL_PROVIDER", "docker-mailserver"),
        mail_server_host=os.getenv("MAIL_SERVER_HOST", ""),
        mail_server_port=int(os.getenv("MAIL_SERVER_PORT", "22")),
        mail_server_user=os.getenv("MAIL_SERVER_USER", "root"),
        mail_server_password=os.getenv("MAIL_SERVER_PASSWORD", ""),
        mail_dms_container=os.getenv("MAIL_DMS_CONTAINER", "mailserver"),
        mail_use_ssh=os.getenv("MAIL_USE_SSH", "false").lower() in ("true", "1", "yes"),
    )


@lru_cache
def get_mail_settings() -> MailSettings:
    if _mail_settings is None:
        register_mail_settings()
    return _mail_settings  # type: ignore[return-value]
