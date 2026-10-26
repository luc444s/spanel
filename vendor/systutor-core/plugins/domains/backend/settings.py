"""Configuración del plugin de dominios.

Reutiliza las variables `SPANEL_PROXY_*` del CLI para que exista una sola
configuración, y agrega solo el perfil y los límites del sondeo.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from plugins.domains.backend.proxy.config import PROFILES, Settings


def _load_env() -> None:
    """Carga `.env` del repo si existe; `os.environ` siempre gana."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if not candidate.is_file():
            continue
        for raw_line in candidate.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                os.environ.setdefault(key, value.strip())
        return


class DomainsSettings(BaseModel):
    profile: str = "public"
    transport: str = "auto"
    proxy_ssh_host: str = ""
    proxy_config_dir: str = ""

    def proxy_settings(self) -> Settings:
        if self.profile not in PROFILES:
            raise ValueError(f"perfil desconocido: {self.profile}")
        settings = Settings.from_env(self.profile)
        # La API puede apuntar el sondeo a otro host que el de escritura.
        return settings

    def is_configured(self) -> bool:
        return bool(self.proxy_ssh_host or self.proxy_config_dir or self.transport != "auto")


_settings: DomainsSettings | None = None


def register_domains_settings() -> None:
    global _settings
    _load_env()
    profile = os.getenv("SPANEL_DOMAINS_PROFILE", "public").strip() or "public"
    _settings = DomainsSettings(
        profile=profile,
        transport=os.getenv("SPANEL_PROXY_TRANSPORT", "auto").strip() or "auto",
        proxy_ssh_host=os.getenv("SPANEL_PROXY_SSH_HOST", "").strip(),
        proxy_config_dir=os.getenv("SPANEL_PROXY_CONFIG_DIR", "").strip(),
    )


@lru_cache
def get_domains_settings() -> DomainsSettings:
    if _settings is None:
        register_domains_settings()
    return _settings  # type: ignore[return-value]
