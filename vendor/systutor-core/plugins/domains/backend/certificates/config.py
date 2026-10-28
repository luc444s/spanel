"""Rutas y umbrales del gestor de certificados.

La web solo necesita leer `state.json`, escribir en `requests/` y leer
`jobs/`. Todo se configura por entorno para no acoplar el plugin al host.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_STATE_PATH = "/var/lib/spanel/cert-manager/state.json"
DEFAULT_REQUESTS_DIR = "/var/lib/spanel/cert-manager/requests"
DEFAULT_JOBS_DIR = "/var/lib/spanel/cert-manager/jobs"
DEFAULT_CERT_NAME = "cf-mail"


def _env_int(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True, slots=True)
class CertificatesConfig:
    """Configuración inmutable del inventario y de las solicitudes."""

    state_path: Path = Path(DEFAULT_STATE_PATH)
    requests_dir: Path = Path(DEFAULT_REQUESTS_DIR)
    jobs_dir: Path = Path(DEFAULT_JOBS_DIR)
    default_name: str = DEFAULT_CERT_NAME
    renew_days: int = 30
    state_max_age_seconds: int = 900

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> CertificatesConfig:
        source = os.environ if env is None else env

        def path(name: str, default: str) -> Path:
            raw = source.get(name, "").strip()
            return Path(raw).expanduser() if raw else Path(default)

        return cls(
            state_path=path("SPANEL_CERT_STATE_PATH", DEFAULT_STATE_PATH),
            requests_dir=path("SPANEL_CERT_REQUESTS_DIR", DEFAULT_REQUESTS_DIR),
            jobs_dir=path("SPANEL_CERT_JOBS_DIR", DEFAULT_JOBS_DIR),
            default_name=(source.get("SPANEL_CERT_DEFAULT_NAME", "").strip() or DEFAULT_CERT_NAME),
            renew_days=_env_int(source, "SPANEL_CERT_RENEW_DAYS", 30),
            state_max_age_seconds=_env_int(source, "SPANEL_CERT_STATE_MAX_AGE", 900),
        )
