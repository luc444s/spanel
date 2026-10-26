"""Pure names, initial content and Docker provisioning for static sites.

The plugin image cannot import ``scripts/spanel_static``; only the minimal parts
needed behind the API are ported here. Everything that derives a name, a label
or the creation argv is pure and testable without a Docker daemon.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .provisioner import Provisioner
from .settings import HostingSettings, docker_env
from .transport import Fallo

KIND = "static"
LABEL_STATIC = "com.spanel.static"
LABEL_DOMAIN = "com.spanel.static.domain"
LABEL_KIND = "spanel.kind"
LABEL_MANAGED = "spanel.managed"
LABEL_SITE = "spanel.site"
LABEL_TENANT = "spanel.tenant"
WEB_ROOT = "/usr/share/nginx/html"
MARKER = 'data-spanel-static="1"'

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


def files_root_for(kind: str, wordpress_root: str) -> str:
    """The filemanager target inside the container for each site kind."""
    return WEB_ROOT if kind == KIND else wordpress_root


@dataclass(frozen=True)
class StaticNames:
    """Names derived from a slug. Nothing is persisted but the container row."""

    slug: str

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
        ]
        if domain:
            rows.append((LABEL_DOMAIN, domain))
        return rows

    def label_args(self, tenant_id: str, domain: str | None = None) -> list[str]:
        args: list[str] = []
        for key, value in self.labels(tenant_id, domain):
            args += ["--label", f"{key}={value}"]
        return args

    def traefik_args(self, domain: str) -> list[str]:
        return [
            "--label",
            "traefik.enable=true",
            "--label",
            f"traefik.http.routers.{self.slug}.rule=Host(`{domain}`)",
            "--label",
            f"traefik.http.services.{self.slug}.loadbalancer.server.port=80",
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
    """The exact argv for the static container; no user data becomes a shell."""
    return [
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
        f"{names.volume}:{WEB_ROOT}",
        settings.static_image,
    ]


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

    def _write(self, container: str, destination: str, content: str) -> None:
        self._docker.command(
            "exec",
            "-i",
            container,
            "sh",
            "-c",
            'mkdir -p "$1" && cat > "$2"',
            "spanel-static",
            WEB_ROOT,
            destination,
            input=content,
        )

    def _seed(self, names: StaticNames) -> None:
        for filename, content in CONTENT.items():
            self._write(names.container, f"{WEB_ROOT}/{filename}", content)
        self._docker.command(
            "exec",
            names.container,
            "chown",
            "-R",
            "nginx:nginx",
            WEB_ROOT,
        )

    def _wait_http(self, names: StaticNames) -> None:
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
                    "http://127.0.0.1/",
                )
                if MARKER in output:
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
    ) -> None:
        """Create volume, container and initial content as one transition."""
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
