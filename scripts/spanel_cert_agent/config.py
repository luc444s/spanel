"""Configuración del agente: `/etc/spanel/cert-agent.toml` + entorno.

Todo lo que ejecuta el agente sale de aquí. El payload de una solicitud solo
aporta hostname y acción; ningún flag de Certbot se toma de la web.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import tomllib

from .validation import CertificateValidationError, normalize_hostname

DEFAULT_CONFIG_PATH = "/etc/spanel/cert-agent.toml"
DEFAULT_STATE_PATH = "/var/lib/spanel/cert-manager/state.json"
DEFAULT_REQUESTS_DIR = "/var/lib/spanel/cert-manager/requests"
DEFAULT_JOBS_DIR = "/var/lib/spanel/cert-manager/jobs"
DEFAULT_INSTALL_ROOT = "/etc/ssl/spanel/mail"
DEFAULT_DEFAULT_NAME = "cf-mail"


class AgentConfigError(Exception):
    """Configuración ausente o inválida: el agente no debe improvisar."""


def _as_command(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(shlex.split(value))
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise AgentConfigError("Comando inválido en cert-agent.toml")


def _as_path(value: object, default: Path | None = None) -> Path | None:
    if value is None or value == "":
        return default
    if not isinstance(value, str):
        raise AgentConfigError("Ruta inválida en cert-agent.toml")
    return Path(value).expanduser()


@dataclass(frozen=True, slots=True)
class CertbotConfig:
    executable: str = "/usr/bin/certbot"
    challenge: str = "auto"
    webroot_path: Path | None = None
    dns_plugin: str | None = None
    lineage_root: Path = Path("/etc/letsencrypt/live")
    ca_file: Path | None = None


@dataclass(frozen=True, slots=True)
class PostfixConfig:
    sni_map_path: Path | None = None
    postmap_command: tuple[str, ...] = ()
    reload_command: tuple[str, ...] = ()

    def configured(self) -> bool:
        return bool(self.sni_map_path and self.postmap_command and self.reload_command)


@dataclass(frozen=True, slots=True)
class DovecotConfig:
    local_name_path: Path | None = None
    reload_command: tuple[str, ...] = ()

    def configured(self) -> bool:
        return bool(self.local_name_path and self.reload_command)


@dataclass(frozen=True, slots=True)
class AgentConfig:
    allowlist: frozenset[str] = frozenset()
    default_name: str = DEFAULT_DEFAULT_NAME
    default_cert_path: Path | None = Path("/etc/ssl/cf-mail.fullchain.crt")
    default_key_path: Path | None = Path("/etc/ssl/cf-mail.key")
    state_path: Path = Path(DEFAULT_STATE_PATH)
    requests_dir: Path = Path(DEFAULT_REQUESTS_DIR)
    jobs_dir: Path = Path(DEFAULT_JOBS_DIR)
    install_root: Path = Path(DEFAULT_INSTALL_ROOT)
    renew_days: int = 30
    certbot: CertbotConfig = field(default_factory=CertbotConfig)
    postfix: PostfixConfig = field(default_factory=PostfixConfig)
    dovecot: DovecotConfig = field(default_factory=DovecotConfig)
    source: str = DEFAULT_CONFIG_PATH

    def require_hostname(self, hostname: str) -> None:
        if hostname == self.default_name:
            raise AgentConfigError("El certificado global no se regenera")
        if hostname not in self.allowlist:
            raise AgentConfigError(f"Hostname fuera de allowlist: {hostname}")

    def require_reload_config(self) -> None:
        if not self.postfix.configured():
            raise AgentConfigError(
                "Falta configuración de Postfix (sni_map_path, postmap_command y reload_command)"
            )
        if not self.dovecot.configured():
            raise AgentConfigError(
                "Falta configuración de Dovecot (local_name_path y reload_command)"
            )


def _env(env: dict[str, str], name: str) -> str | None:
    value = env.get(name)
    return value if value and value.strip() else None


def load_config(
    path: str | os.PathLike[str] | None = None,
    *,
    env: dict[str, str] | None = None,
) -> AgentConfig:
    environment = os.environ if env is None else env
    config_path = Path(
        path or _env(environment, "SPANEL_CERT_AGENT_CONFIG") or DEFAULT_CONFIG_PATH
    )
    raw: dict = {}
    if config_path.exists():
        try:
            raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise AgentConfigError(f"No se pudo leer {config_path}: {exc}") from exc

    allowlist = raw.get("allowlist") or []
    env_allowlist = _env(environment, "SPANEL_CERT_ALLOWLIST")
    if env_allowlist:
        allowlist = [item.strip() for item in env_allowlist.split(",") if item.strip()]
    if not isinstance(allowlist, list):
        raise AgentConfigError("allowlist debe ser una lista")
    normalized_allowlist: set[str] = set()
    for item in allowlist:
        try:
            normalized_allowlist.add(normalize_hostname(item))
        except CertificateValidationError as exc:
            raise AgentConfigError(f"Hostname inválido en allowlist: {item}") from exc
    allowlist = sorted(normalized_allowlist)

    certbot_raw = raw.get("certbot") if isinstance(raw.get("certbot"), dict) else {}
    postfix_raw = raw.get("postfix") if isinstance(raw.get("postfix"), dict) else {}
    dovecot_raw = raw.get("dovecot") if isinstance(raw.get("dovecot"), dict) else {}

    def pick(section: dict, key: str, env_name: str, fallback=None):
        return _env(environment, env_name) or section.get(key, fallback)

    certbot = CertbotConfig(
        executable=pick(certbot_raw, "executable", "SPANEL_CERTBOT_EXECUTABLE", "/usr/bin/certbot"),
        challenge=pick(certbot_raw, "challenge", "SPANEL_CERTBOT_CHALLENGE", "auto"),
        webroot_path=_as_path(
            pick(certbot_raw, "webroot_path", "SPANEL_CERTBOT_WEBROOT", None)
        ),
        dns_plugin=pick(certbot_raw, "dns_plugin", "SPANEL_CERTBOT_DNS_PLUGIN", None),
        lineage_root=_as_path(
            pick(certbot_raw, "lineage_root", "SPANEL_CERTBOT_LINEAGE_ROOT", "/etc/letsencrypt/live")
        )
        or Path("/etc/letsencrypt/live"),
        ca_file=_as_path(pick(certbot_raw, "ca_file", "SPANEL_CERTBOT_CA_FILE", None)),
    )
    if certbot.challenge not in ("auto", "webroot", "dns-01"):
        raise AgentConfigError("challenge debe ser auto, webroot o dns-01")

    postfix = PostfixConfig(
        sni_map_path=_as_path(
            pick(postfix_raw, "sni_map_path", "SPANEL_POSTFIX_SNI_MAP", None)
        ),
        postmap_command=_as_command(
            pick(postfix_raw, "postmap_command", "SPANEL_POSTFIX_POSTMAP_CMD", None)
        ),
        reload_command=_as_command(
            pick(postfix_raw, "reload_command", "SPANEL_POSTFIX_RELOAD_CMD", None)
        ),
    )
    dovecot = DovecotConfig(
        local_name_path=_as_path(
            pick(dovecot_raw, "local_name_path", "SPANEL_DOVECOT_LOCAL_NAME", None)
        ),
        reload_command=_as_command(
            pick(dovecot_raw, "reload_command", "SPANEL_DOVECOT_RELOAD_CMD", None)
        ),
    )

    def int_value(section: dict, key: str, env_name: str, default: int) -> int:
        raw_value = pick(section, key, env_name, default)
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise AgentConfigError(f"{key} debe ser un entero") from exc
        return parsed if parsed > 0 else default

    return AgentConfig(
        allowlist=frozenset(str(item).strip().lower() for item in allowlist if str(item).strip()),
        default_name=str(
            pick(raw, "default_certificate", "SPANEL_CERT_DEFAULT_NAME", DEFAULT_DEFAULT_NAME)
        ),
        default_cert_path=_as_path(
            pick(raw, "default_cert_path", "SPANEL_CERT_DEFAULT_CERT_PATH", "/etc/ssl/cf-mail.fullchain.crt")
        ),
        default_key_path=_as_path(
            pick(raw, "default_key_path", "SPANEL_CERT_DEFAULT_KEY_PATH", "/etc/ssl/cf-mail.key")
        ),
        state_path=_as_path(pick(raw, "state_path", "SPANEL_CERT_STATE_PATH", DEFAULT_STATE_PATH))
        or Path(DEFAULT_STATE_PATH),
        requests_dir=_as_path(
            pick(raw, "requests_dir", "SPANEL_CERT_REQUESTS_DIR", DEFAULT_REQUESTS_DIR)
        )
        or Path(DEFAULT_REQUESTS_DIR),
        jobs_dir=_as_path(pick(raw, "jobs_dir", "SPANEL_CERT_JOBS_DIR", DEFAULT_JOBS_DIR))
        or Path(DEFAULT_JOBS_DIR),
        install_root=_as_path(
            pick(raw, "install_root", "SPANEL_CERT_INSTALL_ROOT", DEFAULT_INSTALL_ROOT)
        )
        or Path(DEFAULT_INSTALL_ROOT),
        renew_days=int_value(raw, "renew_days", "SPANEL_CERT_RENEW_DAYS", 30),
        certbot=certbot,
        postfix=postfix,
        dovecot=dovecot,
        source=str(config_path),
    )
