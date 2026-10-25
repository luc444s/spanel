"""Actualización opcional de home/siteurl de WordPress (solo CLI)."""

from __future__ import annotations

import shlex

from .core import EXIT_WORDPRESS, ProxyError


class WordPressMixin:
    def _update_wordpress(self, site: str, domain: str) -> None:
        url = self.link_for(domain)
        # El wp-cli corre en un contenedor efímero que comparte la red del sitio.
        # Las variables de entorno (incluida la BD) se pasan por archivo temporal
        # 0600, nunca por argv ni por logs.
        script = (
            "umask 077\n"
            "f=$(mktemp)\n"
            f'docker inspect -f {shlex.quote("{{range .Config.Env}}{{println .}}{{end}}")} '
            f'{shlex.quote(site)} > "$f" || {{ rm -f "$f"; exit 4; }}\n'
            "rc=0\n"
        )
        for option in ("home", "siteurl"):
            script += (
                "docker run --rm --volumes-from "
                f"{shlex.quote(site)} --network container:{shlex.quote(site)} "
                f'--env-file "$f" {shlex.quote(self.settings.wp_image)} '
                f"option update {option} {shlex.quote(url)} "
                f"--path={shlex.quote(self.settings.wp_root)} --allow-root || rc=$?\n"
            )
        script += 'rm -f "$f"\nexit "$rc"\n'

        result = self.runner.run(["sh", "-c", script])
        if not result.ok:
            raise ProxyError(
                f"no se pudo actualizar WordPress: {result.text().strip()}",
                EXIT_WORDPRESS,
            )
        self.audit("wordpress", site, domain, "updated")
        self.say(f"wordpress: home y siteurl = {url}")

    # ---- DNS interno ------------------------------------------------------

