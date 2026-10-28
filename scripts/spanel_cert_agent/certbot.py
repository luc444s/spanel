"""Construcción del argv de Certbot.

Solo se permite una forma fija por acción. No se aceptan hooks, flags ni rutas
desde la solicitud (I2).
"""

from __future__ import annotations

from pathlib import Path

from .config import AgentConfig


class CertbotError(Exception):
    pass


def lineage_dir(config: AgentConfig, hostname: str) -> Path:
    return config.certbot.lineage_root / hostname


def lineage_exists(config: AgentConfig, hostname: str) -> bool:
    directory = lineage_dir(config, hostname)
    return (directory / "fullchain.pem").is_file() and (directory / "privkey.pem").is_file()


def _challenge_args(config: AgentConfig) -> list[str]:
    challenge = config.certbot.challenge
    if challenge == "auto":
        return []
    if challenge == "webroot":
        if config.certbot.webroot_path is None:
            raise CertbotError("challenge=webroot requiere webroot_path")
        return ["--webroot", "-w", str(config.certbot.webroot_path)]
    if challenge == "dns-01":
        plugin = (config.certbot.dns_plugin or "").strip()
        # El plugin DNS vive en Certbot; el agente solo nombra el autenticador
        # configurado. Se exige un nombre simple, sin flags.
        if not plugin or not plugin.replace("-", "").replace("_", "").isalnum():
            raise CertbotError("challenge=dns-01 requiere un dns_plugin válido")
        return [f"--dns-{plugin}"]
    raise CertbotError(f"challenge desconocido: {challenge}")


def build_argv(config: AgentConfig, hostname: str, action: str) -> list[str]:
    """Devuelve el argv exacto o falla; nunca acepta nada del payload."""

    executable = config.certbot.executable
    if action == "ensure":
        if lineage_exists(config, hostname):
            # Renovar respeta la configuración de renovación de Certbot.
            return [executable, "renew", "--cert-name", hostname]
        return [
            executable,
            "certonly",
            "--cert-name",
            hostname,
            "-d",
            hostname,
            *_challenge_args(config),
        ]
    if action == "force":
        return [
            executable,
            "certonly",
            "--force-renewal",
            "--cert-name",
            hostname,
            "-d",
            hostname,
            *_challenge_args(config),
        ]
    raise CertbotError("Acción inválida")
