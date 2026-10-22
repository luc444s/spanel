"""Create and clean up only resources bearing this site's exact ownership labels."""

from __future__ import annotations

import re
import secrets
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass

from .inspection import inspect_container
from .settings import HostingSettings, docker_env
from .transport import (
    Fallo,
    borrar_archivo_remoto,
    build_docker_argv,
    escribir_archivo_remoto,
    run,
)


def slug_for(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not re.fullmatch(r"[a-z0-9-]{3,40}", slug):
        raise ValueError("El nombre debe producir un slug de 3 a 40 caracteres")
    return slug


@dataclass(frozen=True)
class SiteNames:
    slug: str

    @property
    def wp(self) -> str:
        return f"spanel-{self.slug}-wp"

    @property
    def db(self) -> str:
        return f"spanel-{self.slug}-db"


class Provisioner:
    def __init__(self, settings: HostingSettings) -> None:
        self.settings = settings
        self.env = docker_env(settings)

    def command(self, *args: str, input: str | None = None) -> str:
        command = "docker " + " ".join(shlex.quote(arg) for arg in args)
        result = run(
            build_docker_argv(command, env=self.env),
            env=self.env,
            input=input,
            timeout=max(self.settings.command_timeout, self.settings.install_timeout),
        )
        if result.returncode:
            # Neither stdin nor the environment file's contents are reflected in errors.
            message = (result.stderr or result.stdout).strip()[:400]
            if input:
                message = message.replace(input.strip(), "<oculto>")
            raise Fallo(f"{command}: {message}")
        return result.stdout + (result.stderr if args and args[0] == "logs" else "")

    def exists(self, kind: str, name: str) -> bool:
        try:
            self.command(kind, "inspect", name)
            return True
        except Fallo as exc:
            # A daemon or transport error must not be treated as an available name.
            if "No such" not in str(exc) and "no such" not in str(exc):
                raise
            return False

    def preflight(self, names: SiteNames) -> None:
        if not self.exists("network", self.settings.network):
            raise Fallo(f"No existe la red {self.settings.network}")
        for name in (names.wp, names.db):
            if self.exists("container", name) or self.exists("volume", name):
                raise Fallo(f"Ya existe un recurso con nombre {name}")

    def _wait(self, action: Callable[[], object], label: str) -> None:
        deadline = time.monotonic() + self.settings.install_timeout
        while True:
            try:
                action()
                return
            except Fallo as exc:
                if time.monotonic() >= deadline:
                    raise Fallo(f"Tiempo agotado esperando {label}: {exc}") from exc
                time.sleep(2)

    def _labels(self, names: SiteNames, tenant_id: str) -> list[str]:
        return [
            item
            for key, value in (
                ("spanel.site", names.slug),
                ("spanel.managed", "true"),
                ("spanel.tenant", tenant_id),
            )
            for item in ("--label", f"{key}={value}")
        ]

    def create(self, names: SiteNames, tenant_id: str, domain: str, admin_email: str) -> str:
        """The caller preflights DB uniqueness. On failure remove only owned resources."""
        self.preflight(names)
        password = secrets.token_urlsafe(24)
        db_password = secrets.token_urlsafe(24)
        root_password = secrets.token_urlsafe(24)
        db_name = "wp_" + secrets.token_hex(5)
        db_user = "wp_" + secrets.token_hex(5)
        labels = self._labels(names, tenant_id)
        files: list[str] = []
        created: list[tuple[str, str]] = []
        try:
            db_file = escribir_archivo_remoto(
                self.env,
                (
                    f"MYSQL_DATABASE={db_name}\nMYSQL_USER={db_user}\n"
                    f"MYSQL_PASSWORD={db_password}\nMYSQL_ROOT_PASSWORD={root_password}\n"
                ),
                "hosting-db",
            )
            files.append(db_file)
            wp_file = escribir_archivo_remoto(
                self.env,
                (
                    f"WORDPRESS_DB_HOST={names.db}\nWORDPRESS_DB_USER={db_user}\n"
                    f"WORDPRESS_DB_PASSWORD={db_password}\nWORDPRESS_DB_NAME={db_name}\n"
                    f"MYSQL_PWD={db_password}\n"
                ),
                "hosting-wp",
            )
            files.append(wp_file)
            for name in (names.db, names.wp):
                created.append(("volume", name))
                self.command("volume", "create", *labels, name)
            created.append(("container", names.db))
            self.command(
                "run",
                "-d",
                "--name",
                names.db,
                "--network",
                self.settings.network,
                *labels,
                "--env-file",
                db_file,
                "-v",
                f"{names.db}:/var/lib/mysql",
                self.settings.db_image,
            )

            def ready() -> None:
                self.command(
                    "run",
                    "--rm",
                    "--network",
                    self.settings.network,
                    "--env-file",
                    wp_file,
                    self.settings.db_image,
                    "mariadb",
                    "-h",
                    names.db,
                    "-u",
                    db_user,
                    db_name,
                    "-e",
                    "SELECT 1",
                )

            self._wait(ready, "MariaDB")
            traefik = (
                [
                    "--label",
                    "traefik.enable=true",
                    "--label",
                    f"traefik.http.routers.{names.slug}.rule=Host(`{domain}`)",
                    "--label",
                    f"traefik.http.services.{names.slug}.loadbalancer.server.port=80",
                ]
                if self.settings.traefik
                else []
            )
            created.append(("container", names.wp))
            self.command(
                "run",
                "-d",
                "--name",
                names.wp,
                "--network",
                self.settings.network,
                *labels,
                *traefik,
                "--env-file",
                wp_file,
                "-v",
                f"{names.wp}:/var/www/html",
                self.settings.wp_image,
            )
            cli = (
                "run",
                "--rm",
                "--network",
                self.settings.network,
                "--volumes-from",
                names.wp,
                "--env-file",
                wp_file,
                "wordpress:cli",
            )
            self._wait(lambda: self.command(*cli, "wp", "core", "version"), "archivos WordPress")
            self.command(
                "run",
                "-i",
                "--rm",
                "--network",
                self.settings.network,
                "--volumes-from",
                names.wp,
                "--env-file",
                wp_file,
                "wordpress:cli",
                "wp",
                "core",
                "install",
                f"--url=http://{domain}",
                f"--title={names.slug}",
                "--admin_user=admin",
                f"--admin_email={admin_email}",
                "--prompt=admin_password",
                input=password + "\n",
            )
            self._wait(
                lambda: self.command(*cli, "wp", "core", "is-installed"),
                "instalacion WordPress",
            )
            return password
        except Exception as exc:
            failures = self.cleanup(names, tenant_id, created)
            if failures:
                raise Fallo(f"{exc}; limpieza incompleta: {'; '.join(failures)}") from exc
            raise
        finally:
            borrar_archivo_remoto(self.env, *files)

    def _owned(self, kind: str, name: str, names: SiteNames, tenant_id: str) -> bool:
        if kind == "container":
            info = inspect_container(self.env, name, timeout=self.settings.command_timeout)
            labels = ((info or {}).get("Config") or {}).get("Labels") or {}
        else:
            import json

            try:
                info = json.loads(self.command("volume", "inspect", name))[0]
            except Fallo:
                return False
            labels = info.get("Labels") or {}
        return labels.get("spanel.site") == names.slug and labels.get("spanel.tenant") == tenant_id

    def cleanup(
        self, names: SiteNames, tenant_id: str, resources: list[tuple[str, str]] | None = None
    ) -> list[str]:
        resources = (
            resources
            if resources is not None
            else [
                ("volume", names.db),
                ("volume", names.wp),
                ("container", names.db),
                ("container", names.wp),
            ]
        )
        failures: list[str] = []
        for kind, name in reversed(resources):
            try:
                if self._owned(kind, name, names, tenant_id):
                    self.command("rm", "-f", name) if kind == "container" else self.command(
                        "volume", "rm", name
                    )
            except Exception as exc:
                failures.append(f"{kind} {name}: {exc}")
        return failures
