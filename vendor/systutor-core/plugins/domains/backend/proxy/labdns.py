"""DNS interno del laboratorio (solo perfil internal)."""

from __future__ import annotations

from collections.abc import Sequence

from .core import EXIT_TRANSPORT, ProxyError


class LabDnsMixin:
    def _dns_plan(self, domains: Sequence[str]) -> list[str]:
        if not self.settings.is_internal or self.settings.dns_hosts_file is None:
            return []
        return [f"dns: agregar {domain} en {self.settings.dns_hosts_file}" for domain in domains]

    def _dns_apply(self, domains: Sequence[str], *, add: bool) -> None:
        path = self.settings.dns_hosts_file
        if not self.settings.is_internal or path is None:
            return
        ip = self.container_ip(self.settings.traefik_container)
        if add and not ip:
            raise ProxyError(
                "no se pudo obtener la IP del Traefik del laboratorio", EXIT_TRANSPORT
            )
        text = self._read_text(path) or ""
        targets = set(domains)
        kept: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                kept.append(line)
                continue
            parts = stripped.split()
            if len(parts) >= 2 and parts[-1] in targets:
                continue
            kept.append(line)
        if add:
            for domain in domains:
                kept.append(f"{ip} {domain}")
        self._write_text(path, "\n".join(kept).rstrip("\n") + "\n")
        self.docker("kill", "-s", "HUP", self.settings.dns_container, check=False)

    # ---- búsqueda y respaldo ---------------------------------------------

