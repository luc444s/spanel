"""Per-site configuration: the canonical model and the first adapter.

The panel does not store "an env-file" or "a docker-compose": it stores a
desired state (editable environment variables plus named secrets) and an
adapter renders it to the format a given site actually uses. `RawAdapter`
covers the containers created with `docker run`, which is every site that
exists today. A compose adapter can implement the same protocol later without
touching the routes.

Two rules keep this safe:

1. Only a small whitelist of environment variables is editable. The keys that
   point at the database (`WORDPRESS_DB_*`, `MYSQL_PWD`) are secrets, not
   settings: a tenant that could rewrite them could point its site at another
   tenant's database on the shared Docker network.
2. Applying a change recreates the container from its own `docker inspect`,
   never from a hand-built command. The named volumes are reused as-is, so the
   site's data is never touched. Docker keeps environment variables immutable
   for a container's lifetime, so a recreate is the only correct way to change
   them; a plain restart would silently keep the old values.
"""

from __future__ import annotations

import re

from systutor.core.errors import AppError

from .inspection import inspect_container
from .models import HostingSite
from .provisioner import Provisioner
from .settings import HostingSettings, docker_env
from .transport import Fallo, escribir_archivo_remoto_en

# The env keys a tenant may change. Everything else is either image plumbing
# or a secret. A key absent from this map is rejected, not ignored.
ENV_WHITELIST: dict[str, re.Pattern[str]] = {
    "PHP_MEMORY_LIMIT": re.compile(r"^\d+[KMG]?$", re.IGNORECASE),
    "WP_MEMORY_LIMIT": re.compile(r"^\d+[KMG]?$", re.IGNORECASE),
    "UPLOAD_LIMIT": re.compile(r"^\d+[KMG]?$", re.IGNORECASE),
    "WP_DEBUG": re.compile(r"^(true|false)$", re.IGNORECASE),
    "WP_DEBUG_LOG": re.compile(r"^(true|false)$", re.IGNORECASE),
    "WP_DEBUG_DISPLAY": re.compile(r"^(true|false)$", re.IGNORECASE),
    "WP_POST_REVISIONS": re.compile(r"^(\d+|false)$", re.IGNORECASE),
    "WP_AUTO_UPDATE_CORE": re.compile(r"^(true|false|minor)$", re.IGNORECASE),
    "TZ": re.compile(r"^[A-Za-z_]+/[A-Za-z0-9_+\-]+$"),
}

# The names of the secrets this plugin knows about. Values are never returned
# by a route; only the names are, so the UI can say what exists.
SECRET_KEYS: tuple[str, ...] = ("WORDPRESS_DB_PASSWORD", "MYSQL_PWD")


def parse_env(text: str) -> dict[str, str]:
    """Parse a KEY=VALUE file, ignoring comments and blank lines."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def render_env(values: dict[str, str]) -> str:
    """Render a stable env file: sorted keys, one KEY=VALUE per line."""
    return "".join(f"{key}={values[key]}\n" for key in sorted(values))


def validate_env_updates(updates: dict[str, str]) -> dict[str, str]:
    """Keep only whitelisted keys with a valid value, or fail naming the key."""
    cleaned: dict[str, str] = {}
    for key, value in updates.items():
        pattern = ENV_WHITELIST.get(key)
        if pattern is None:
            raise AppError(
                f"La variable {key} no se puede editar desde el panel",
                status_code=422,
                code="hosting_invalid_env",
            )
        text = str(value).strip()
        if "\n" in text or "\r" in text:
            raise AppError(
                f"El valor de {key} no puede tener saltos de linea",
                status_code=422,
                code="hosting_invalid_env",
            )
        if not pattern.fullmatch(text):
            raise AppError(
                f"El valor de {key} no es valido",
                status_code=422,
                code="hosting_invalid_env",
            )
        cleaned[key] = text
    return cleaned


def editable_env(env: dict[str, str]) -> dict[str, str]:
    """The whitelisted keys present in an environment, values included."""
    return {key: env[key] for key in ENV_WHITELIST if key in env}


def secret_names(env: dict[str, str]) -> tuple[str, ...]:
    """The names of the secrets present, never their values."""
    return tuple(sorted(key for key in SECRET_KEYS if key in env))


def _mapping(value: object) -> dict:
    """A dict for a real dict, an empty one for anything else."""
    return value if isinstance(value, dict) else {}


def env_of(info: dict) -> dict[str, str]:
    """The real container environment from a `docker inspect` payload."""
    config = info.get("Config")
    config = config if isinstance(config, dict) else {}
    pairs = config.get("Env")
    pairs = pairs if isinstance(pairs, list) else []
    env: dict[str, str] = {}
    for pair in pairs:
        if not isinstance(pair, str):
            continue
        key, _, value = pair.partition("=")
        if key:
            env[key.strip()] = value.strip()
    return env


def site_dir(settings: HostingSettings, container_name: str) -> str:
    """The stable directory on the Docker host that belongs to one site."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", container_name.strip().lower()).strip("-")
    return f"{settings.sites_dir.rstrip('/')}/{slug}"


def env_path(settings: HostingSettings, container_name: str) -> str:
    return f"{site_dir(settings, container_name)}/wp.env"


def build_run_args(info: dict, *, name: str, env_file: str) -> list[str]:
    """Rebuild the `docker run` arguments from the container's own inspect.

    Pure on purpose: the destructive part (`rm -f` + `run`) is a thin call, so
    the argument reconstruction can be tested without Docker. The named
    volumes are passed through by name, which is why a recreate never touches
    the site's data.
    """
    config = _mapping(info.get("Config"))
    host = _mapping(info.get("HostConfig"))
    image = str(config.get("Image") or info.get("Image") or "").strip()
    if not image:
        raise Fallo("el contenedor no declara imagen; no se puede recrear")

    args: list[str] = ["-d", "--name", name]

    network = str(host.get("NetworkMode") or "").strip()
    if network and network not in {"default", "bridge"}:
        args += ["--network", network]

    labels = _mapping(config.get("Labels"))
    for key in sorted(labels):
        args += ["--label", f"{key}={labels[key]}"]

    mounts = info.get("Mounts")
    for mount in mounts if isinstance(mounts, list) else []:
        if not isinstance(mount, dict):
            continue
        kind = mount.get("Type")
        destination = mount.get("Destination")
        if kind == "volume" and mount.get("Name"):
            source = mount["Name"]
        elif kind == "bind" and mount.get("Source"):
            source = mount["Source"]
        else:
            continue
        spec = f"{source}:{destination}"
        if mount.get("RW") is False:
            spec += ":ro"
        args += ["-v", spec]

    restart = _mapping(host.get("RestartPolicy"))
    restart_name = str(restart.get("Name") or "").strip()
    if restart_name and restart_name != "no":
        args += ["--restart", restart_name]

    port_bindings = _mapping(host.get("PortBindings"))
    if port_bindings:
        for container_port in sorted(port_bindings):
            for binding in port_bindings[container_port] or []:
                if not isinstance(binding, dict):
                    continue
                host_port = str(binding.get("HostPort") or "").strip()
                args += ["-p", f"{host_port}:{container_port}" if host_port else container_port]

    args += ["--env-file", env_file, image]

    command = config.get("Cmd")
    if isinstance(command, list):
        args += [str(part) for part in command]
    return args


class HostingConfig:
    """The canonical, non-secret view of a site's configuration."""

    def __init__(
        self,
        *,
        site_id: str,
        container_name: str,
        editable: dict[str, str],
        secrets: tuple[str, ...],
    ) -> None:
        self.site_id = site_id
        self.container_name = container_name
        self.editable = editable
        self.secrets = secrets


class RawAdapter:
    """Configuration for containers created with `docker run`."""

    def __init__(self, settings: HostingSettings) -> None:
        self.settings = settings
        self.env = docker_env(settings)
        self.provisioner = Provisioner(settings)

    def _inspect(self, site: HostingSite) -> dict:
        info = inspect_container(
            self.env, site.container_name, timeout=self.settings.command_timeout
        )
        if info is None:
            raise AppError(
                f"El contenedor {site.container_name} no existe o no responde",
                status_code=409,
                code="hosting_site_unavailable",
            )
        return info

    def read_config(self, site: HostingSite) -> HostingConfig:
        env = env_of(self._inspect(site))
        return HostingConfig(
            site_id=site.id,
            container_name=site.container_name,
            editable=editable_env(env),
            secrets=secret_names(env),
        )

    def _ensure_env_file(self, site: HostingSite, info: dict) -> str:
        """Write the stable env file the recreate and wp-cli will use."""
        path = env_path(self.settings, site.container_name)
        escribir_archivo_remoto_en(self.env, path, render_env(env_of(info)))
        return path

    def update_config(self, site: HostingSite, updates: dict[str, str]) -> HostingConfig:
        """Merge whitelisted changes into the stable env file and recreate."""
        cleaned = validate_env_updates(updates)
        info = self._inspect(site)
        env = env_of(info)
        env.update(cleaned)

        path = env_path(self.settings, site.container_name)
        escribir_archivo_remoto_en(self.env, path, render_env(env))

        args = build_run_args(info, name=site.container_name, env_file=path)
        try:
            self.provisioner.command("rm", "-f", site.container_name)
        except Fallo as exc:
            if "No such" not in str(exc) and "no such" not in str(exc):
                raise
        self.provisioner.command("run", *args)
        return self.read_config(site)

    def rotate_salts(self, site: HostingSite) -> None:
        """Rotate the WordPress authentication salts without recreating.

        `wp-config.php` lives in the site volume and PHP reads it per request,
        so shuffling the salts needs no restart and no recreate: the next
        request already uses the new keys. This is the safe secret rotation;
        the database password is deliberately a different, harder operation.
        """
        info = self._inspect(site)
        path = self._ensure_env_file(site, info)
        # `wordpress:cli` runs as its own www-data (uid 82), but the volume is
        # owned by the Apache image's www-data (uid 33). Copying the file as 82
        # fails with "wp-config.php is not writable", so run as the owner.
        owner = self.provisioner.command(
            "exec", site.container_name, "stat", "-c", "%u", "/var/www/html"
        ).strip()
        self.provisioner.command(
            "run",
            "--rm",
            "--user",
            owner or "33",
            "--env",
            "HOME=/tmp",
            "--network",
            self.settings.network,
            "--volumes-from",
            site.container_name,
            "--env-file",
            path,
            "wordpress:cli",
            "wp",
            "config",
            "shuffle-salts",
        )
