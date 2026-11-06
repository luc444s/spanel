"""Pure names, initial content and Docker provisioning for static sites.

The plugin image cannot import ``scripts/spanel_static``; only the minimal parts
needed behind the API are ported here. Everything that derives a name, a label
or the creation argv is pure and testable without a Docker daemon.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .profiles import ProfileSpec, marker_for
from .provisioner import Provisioner
from .settings import HostingSettings, docker_env
from .transport import Fallo

KIND = "static"
LABEL_STATIC = "com.spanel.static"
LABEL_DOMAIN = "com.spanel.static.domain"
LABEL_KIND = "spanel.kind"
LABEL_PROFILE = "spanel.profile"
LABEL_MANAGED = "spanel.managed"
LABEL_SITE = "spanel.site"
LABEL_TENANT = "spanel.tenant"
WEB_ROOT = "/usr/share/nginx/html"
MARKER = 'data-spanel-static="1"'
DEFAULT_PROFILE_ID = "nginx"

INDEX_HTML = f"""<!doctype html>
<html lang="es" {MARKER}>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sitio estático</title>
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <main class="wrap">
    <p class="kicker">SPanel static</p>
    <h1>Tu sitio está listo</h1>
    <p class="lead">
      Esta página inicial vive en el volumen del sitio. Editala desde el
      administrador de archivos.
    </p>
    <button id="counter" type="button">Probar JavaScript <span id="count">0</span></button>
  </main>
  <script src="/app.js" defer></script>
</body>
</html>
"""

STYLES_CSS = """/* Contenido inicial del sitio estático. */
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: #0b1020;
  color: #e8edf7;
  font-family: ui-sans-serif, system-ui, sans-serif;
}
.wrap { max-width: 42rem; padding: 2rem; }
.kicker { color: #6ee7ff; letter-spacing: .18em; text-transform: uppercase; }
h1 { font-size: clamp(2rem, 7vw, 3.5rem); margin: .5rem 0 1rem; }
.lead { color: #9aa7bd; line-height: 1.6; }
button {
  margin-top: 1rem;
  border: 0;
  border-radius: .7rem;
  padding: .7rem 1rem;
  background: #6ee7ff;
  color: #04121a;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
#count { font-variant-numeric: tabular-nums; }
"""

APP_JS = """/* Comprueba que HTML, CSS y JavaScript se sirven de verdad. */
(() => {
  const button = document.getElementById("counter");
  const count = document.getElementById("count");
  let clicks = 0;
  button?.addEventListener("click", () => {
    clicks += 1;
    if (count) count.textContent = String(clicks);
  });
})();
"""

CONTENT: dict[str, str] = {
    "index.html": INDEX_HTML,
    "styles.css": STYLES_CSS,
    "app.js": APP_JS,
}


def files_root_for(kind: str, wordpress_root: str, profile: ProfileSpec | None = None) -> str:
    """The filemanager target inside the container for each site kind.

    A static site with a resolved profile serves from the profile's web
    root; the builtin nginx keeps its historical constant as the default.
    """
    if kind == KIND:
        return profile.web_root if profile is not None else WEB_ROOT
    return wordpress_root


def index_html_for(profile_id: str) -> str:
    """The seed page carrying BOTH markers: the historical and the profile's.

    The profile marker is the health-check key; the historical marker keeps
    the file recognizable for every tool that matched on it before 0042.
    """
    return INDEX_HTML.replace(
        MARKER,
        f"{MARKER} {marker_for(profile_id)}",
        1,
    )


def profiled_content(profile_id: str) -> dict[str, str]:
    """The initial content of a seeded static site, with its own marker."""
    return {
        **CONTENT,
        "index.html": index_html_for(profile_id),
    }


@dataclass(frozen=True)
class StaticNames:
    """Names derived from a slug. Nothing is persisted but the container row.

    The profile travels with the names so every argv, label and mount is
    derived from ONE object; the default is the builtin nginx profile.
    """

    slug: str
    profile: str = DEFAULT_PROFILE_ID
    web_root: str = WEB_ROOT
    container_port: int = 80
    health_mode: str = "marker"
    image: str = "nginx:alpine"
    env: dict[str, str] = field(default_factory=dict)
    command: str | None = None

    @classmethod
    def for_profile(cls, slug: str, spec: ProfileSpec | None) -> StaticNames:
        """One names object from a resolved spec (or the builtin default)."""
        if spec is None:
            return cls(slug)
        return cls(
            slug,
            profile=spec.id,
            web_root=spec.web_root,
            container_port=spec.container_port,
            health_mode=spec.health_mode,
            image=spec.image,
            env=dict(spec.env),
            command=spec.command,
        )

    @property
    def container(self) -> str:
        return f"spanel-{self.slug}-web"

    @property
    def volume(self) -> str:
        return self.container

    @property
    def service(self) -> str:
        return self.container

    def labels(self, tenant_id: str, domain: str | None = None) -> list[tuple[str, str]]:
        rows = [
            (LABEL_SITE, self.slug),
            (LABEL_MANAGED, "true"),
            (LABEL_TENANT, tenant_id),
            (LABEL_KIND, KIND),
            (LABEL_STATIC, "true"),
            (LABEL_PROFILE, self.profile),
        ]
        if domain:
            rows.append((LABEL_DOMAIN, domain))
        return rows

    def label_args(self, tenant_id: str, domain: str | None = None) -> list[str]:
        args: list[str] = []
        for key, value in self.labels(tenant_id, domain):
            args += ["--label", f"{key}={value}"]
        return args

    def traefik_args(self, domain: str, port: int | None = None) -> list[str]:
        resolved = port if port is not None else self.container_port
        return [
            "--label",
            "traefik.enable=true",
            "--label",
            f"traefik.http.routers.{self.slug}.rule=Host(`{domain}`)",
            "--label",
            f"traefik.http.services.{self.slug}.loadbalancer.server.port={resolved}",
        ]


def build_static_volume_args(names: StaticNames, tenant_id: str, domain: str) -> list[str]:
    return ["volume", "create", *names.label_args(tenant_id, domain), names.volume]


def build_static_run_args(
    names: StaticNames,
    settings: HostingSettings,
    tenant_id: str,
    domain: str,
    *,
    traefik: bool = True,
) -> list[str]:
    """The exact argv for the static container; no user data becomes a shell.

    The builtin nginx profile reproduces the A.SPEC 0031 argv; any other
    profile swaps the image, the mount point, the Traefik port, the env and
    the boot command. `sh -c` runs only for profiles WITH a command, and the
    command is admin-authored, never tenant input.
    """
    args: list[str] = [
        "run",
        "-d",
        "--name",
        names.container,
        "--network",
        settings.network,
        "--restart",
        "unless-stopped",
        *names.label_args(tenant_id, domain),
        *(names.traefik_args(domain) if traefik else []),
        "-v",
        f"{names.volume}:{names.web_root}",
    ]
    for key, value in names.env.items():
        args += ["--env", f"{key}={value}"]
    args.append(names.image)
    if names.command:
        args += ["sh", "-c", names.command]
    return args


class StaticProvisioner:
    """Create only resources bearing this site's exact ownership labels."""

    def __init__(self, settings: HostingSettings) -> None:
        self.settings = settings
        self.env = docker_env(settings)
        self._docker = Provisioner(settings)

    def _exists(self, kind: str, name: str) -> bool:
        return self._docker.exists(kind, name)

    def _preflight(self, names: StaticNames) -> None:
        if not self._exists("network", self.settings.network):
            raise Fallo(f"No existe la red {self.settings.network}")
        for kind, name in (("container", names.container), ("volume", names.volume)):
            if self._exists(kind, name):
                raise Fallo(f"Ya existe un recurso con nombre {name}")

    def _write(self, container: str, destination: str, content: str, web_root: str) -> None:
        self._docker.command(
            "exec",
            "-i",
            container,
            "sh",
            "-c",
            'mkdir -p "$1" && cat > "$2"',
            "spanel-static",
            web_root,
            destination,
            input=content,
        )

    def _seed(self, names: StaticNames) -> None:
        # A freshly created volume starts EMPTY (preflight refuses to reuse
        # one), so the seed ALWAYS writes the site's marker page: the
        # image's welcome page is overwritten and the profile marker is the
        # health check key. Content stays coherent in a single boot.
        for filename, content in profiled_content(names.profile).items():
            self._write(names.container, f"{names.web_root}/{filename}", content, names.web_root)
        # Best effort: the builtin nginx ships the `nginx` user, but other
        # profiles (Caddy, httpd, Node) have their own; the filemanager runs
        # as the container user anyway.
        try:
            self._docker.command(
                "exec",
                names.container,
                "chown",
                "-R",
                "nginx:nginx",
                names.web_root,
            )
        except Fallo:
            pass

    def _wait_http(self, names: StaticNames) -> None:
        marker = marker_for(names.profile)
        http_200 = names.health_mode == "http_200"
        deadline = time.monotonic() + self.settings.install_timeout
        while True:
            try:
                output = self._docker.command(
                    "exec",
                    names.container,
                    "wget",
                    "-q",
                    "-O",
                    "-",
                    f"http://127.0.0.1:{names.container_port}/",
                )
                if http_200 or marker in output or MARKER in output:
                    return
            except Fallo:
                pass
            if time.monotonic() >= deadline:
                raise Fallo(
                    f"El contenedor {names.container} no sirvió la página inicial "
                    f"en {self.settings.install_timeout}s"
                )
            time.sleep(1)

    def create(
        self,
        names: StaticNames,
        tenant_id: str,
        domain: str,
        *,
        traefik: bool = True,
        profile: ProfileSpec | None = None,
    ) -> None:
        """Create volume, container and initial content as one transition."""
        if profile is None:
            names = StaticNames(names.slug, image=self.settings.static_image)
        else:
            names = StaticNames.for_profile(names.slug, profile)
        self._preflight(names)
        created: list[tuple[str, str]] = []
        try:
            created.append(("volume", names.volume))
            self._docker.command(*build_static_volume_args(names, tenant_id, domain))
            created.append(("container", names.container))
            self._docker.command(
                *build_static_run_args(
                    names,
                    self.settings,
                    tenant_id,
                    domain,
                    traefik=traefik,
                )
            )
            self._seed(names)
            self._wait_http(names)
        except Exception as exc:
            failures = self.cleanup(names, tenant_id, created)
            if failures:
                raise Fallo(f"{exc}; limpieza incompleta: {'; '.join(failures)}") from exc
            raise

    def cleanup(
        self,
        names: StaticNames,
        tenant_id: str,
        resources: list[tuple[str, str]] | None = None,
    ) -> list[str]:
        resources = (
            resources
            if resources is not None
            else [
                ("volume", names.volume),
                ("container", names.container),
            ]
        )
        return self._docker.cleanup(names, tenant_id, resources)
