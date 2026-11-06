"""Registro de perfiles estáticos: puro, sin Docker ni I/O.

Cada perfil es la definición COMPLETA de cómo provisionar un sitio: los argv
de Docker, los archivos iniciales y su health check derivan de aquí. Cambiar
un perfil se prueba sin daemon; ejecutarlo es cuestión del provisioner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# El sistema de archivos del contenedor que los perfiles sirven.
PLAIN_ROOT = "/usr/share/nginx/html"
CADDY_ROOT = "/usr/share/caddy"
APACHE_ROOT = "/usr/local/apache2/htdocs"
ASTRO_ROOT = "/site"

LABEL_SITE = "spanel.site"
LABEL_MANAGED = "spanel.managed"
LABEL_KIND = "spanel.kind"
LABEL_PROFILE = "spanel.profile"
LABEL_DOMAIN = "com.spanel.static.domain"
LABEL_LAB = "com.spanel.lab"
KIND = "static"

# Cada perfil expone su contenido con UNA firma distinta. Es el marcador que
# exige el health check interno y el HTTPS externo; la 0031 llama a esto
# "marker" y aquí cada perfil trae el suyo.
MARKER_ATTR = "data-spanel-profile"


@dataclass(frozen=True)
class ProfileSpec:
    """Cómo provisionar un tipo de estático, y nada más."""

    slug: str
    domain: str
    image: str
    web_root: str
    files: dict[str, str] = field(default_factory=dict)
    # Ninguno por defecto: el CMD de la imagen decide. `astro` lo overridea.
    command: str | None = None
    workdir: str | None = None
    # Nombre del usuario/dueno para `chown -R` tras sembrar (nginx lo pide).
    chown_to: str | None = None

    @property
    def marker(self) -> str:
        return f'{MARKER_ATTR}="{self.slug}"'

    @property
    def wait_path(self) -> str | None:
        wf = self.wait_file
        return f"{self.web_root}/{wf}" if wf else None

    @property
    def container(self) -> str:
        return f"spanel-{self.slug}-web"

    @property
    def volume(self) -> str:
        return self.container

    def labels(self) -> list[tuple[str, str]]:
        return [
            (LABEL_SITE, self.slug),
            (LABEL_MANAGED, "true"),
            (LABEL_KIND, KIND),
            (LABEL_PROFILE, self.slug),
            (LABEL_DOMAIN, self.domain),
            (LABEL_LAB, "true"),
        ]

    def label_args(self) -> list[str]:
        args: list[str] = []
        for key, value in self.labels():
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


VALID_SLUG = re.compile(r"^[a-z][a-z0-9]{2,30}$")


def _plain_index() -> str:
    return """<!doctype html>
<html lang="es" data-spanel-profile="plain">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Perfil plain (nginx)</title>
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <main class="wrap">
    <p class="kicker">SPanel · perfil plain</p>
    <h1>Sitio estático vanilla</h1>
    <p class="lead">nginx:alpine + HTML/CSS/JS escritos a mano, el seed de la A.SPEC 0031.</p>
    <button id="counter" type="button">Probar JavaScript <span id="count">0</span></button>
  </main>
  <script src="/app.js" defer></script>
</body>
</html>
"""


def _react_index() -> str:
    return """<!doctype html>
<html lang="es" data-spanel-profile="react">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Perfil react (Caddy)</title>
  <script src="https://unpkg.com/react@18/umd/react.production.min.js" crossorigin></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js" crossorigin></script>
</head>
<body>
  <div id="root"></div>
  <script>
    const e = React.createElement;
    function App() {
      const [n, setN] = React.useState(0);
      return e("div", {
        style: {
          fontFamily: "ui-sans-serif, system-ui",
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          textAlign: "center",
          color: "#e8edf7",
          background: "#0b1020",
        }
      },
        e("p", { style: { letterSpacing: ".2em", textTransform: "uppercase", color: "#087f8c" } }, "SPanel \u00b7 perfil react"),
        e("h1", null, "SPA React 18 en Caddy"),
        e("button", {
          onClick: () => setN(n + 1),
          style: { padding: ".7rem 1rem", border: 0, borderRadius: ".7rem", background: "#087f8c", color: "#fff", cursor: "pointer", fontWeight: 700 }
        }, "Clics: " + n));
    }
    ReactDOM.createRoot(document.getElementById("root")).render(e(App));
  </script>
</body>
</html>
"""


def _boot_index() -> str:
    return """<!doctype html>
<html lang="es" data-spanel-profile="boot">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Perfil boot (Apache)</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-dark text-white">
  <nav class="navbar navbar-expand navbar-dark bg-black px-3">
    <span class="navbar-brand">SPanel</span>
    <div class="navbar-nav">
      <a class="nav-link active" href="#">Inicio</a>
      <a class="nav-link" href="#cta">Empieza</a>
    </div>
  </nav>
  <main class="py-5 text-center">
    <div class="container py-5">
      <span class="badge text-bg-info text-uppercase mb-3">SPanel \u00b7 perfil boot</span>
      <h1 class="display-4">Landing con Bootstrap 5</h1>
      <p class="lead col-md-7 mx-auto text-body-secondary">
        httpd:alpine + Bootstrap 5 por CDN: otro motor, otro stack de UI.
      </p>
      <a id="cta" class="btn btn-info btn-lg mt-3" href="https://spanel">Probar el hosting</a>
    </div>
  </main>
  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""


def _astro_package() -> str:
    return """{
  "name": "spanel-astro-profile",
  "private": true,
  "type": "module",
  "scripts": {
    "build": "astro build",
    "preview": "astro preview"
  },
  "dependencies": { "astro": "^7.4.7" }
}
"""


def _astro_config(domain: str) -> str:
    # Astro 4.16 no pasa `preview.allowedHosts` a Vite: la validación del Host
    # la hace el propio Vite en `vite.preview.allowedHosts`. Se declaran solo
    # los dominios que este perfil va a servir.
    return (
        'import { defineConfig } from "astro/config";\n'
        "export default defineConfig({\n"
        "  server: { host: true },\n"
        "  vite: { preview: { allowedHosts: [" + f'"{domain}"' + "] } },\n"
        "});\n"
    )


def _astro_index() -> str:
    return """---
const marco = "SPanel · perfil astro";
---
<!doctype html>
<html lang="es" data-spanel-profile="astro">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Perfil astro (Node 20)</title>
  </head>
  <body
    style="font-family: ui-sans-serif, system-ui; background: #0b1020; color: #e8edf7;
           display: grid; place-items: center; min-height: 100vh; text-align: center;"
  >
    <main>
      <p style="letter-spacing:.2em; text-transform:uppercase; color:#ff5d01;">{marco}</p>
      <h1>Sitio Astro compilado</h1>
      <p>node:20-alpine + npm install + astro build + astro preview en el puerto 80.</p>
    </main>
  </body>
</html>
"""


PLAIN_STYLES = """/* Seed vanilla del perfil plain. */
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; min-height: 100vh; display: grid; place-items: center;
  background: #0b1020; color: #e8edf7;
  font-family: ui-sans-serif, system-ui, sans-serif;
}
.wrap { max-width: 42rem; padding: 2rem; text-align: center; }
.kicker { color: #6ee7ff; letter-spacing: .18em; text-transform: uppercase; }
h1 { font-size: clamp(2rem, 7vw, 3.5rem); margin: .5rem 0 1rem; }
.lead { color: #9aa7bd; }
button {
  margin-top: 1rem; border: 0; border-radius: .7rem; padding: .7rem 1rem;
  background: #6ee7ff; color: #04121a; font: inherit; font-weight: 700; cursor: pointer;
}
#count { font-variant-numeric: tabular-nums; }
"""

PLAIN_JS = """(() => {
  const button = document.getElementById("counter");
  const count = document.getElementById("count");
  let clicks = 0;
  button?.addEventListener("click", () => {
    clicks += 1;
    if (count) count.textContent = String(clicks);
  });
})();
"""


def build_profiles() -> dict[str, ProfileSpec]:
    """Los perfiles de la verificación; el mismo contrato que 0042 definirá."""
    return {
        "plain": ProfileSpec(
            slug="plain",
            domain="plain.spanel",
            image="nginx:alpine",
            web_root=PLAIN_ROOT,
            files={
                "index.html": _plain_index(),
                "styles.css": PLAIN_STYLES,
                "app.js": PLAIN_JS,
            },
            chown_to="nginx:nginx",
        ),
        "react": ProfileSpec(
            slug="react",
            domain="react.spanel",
            image="caddy:2-alpine",
            web_root=CADDY_ROOT,
            files={"index.html": _react_index()},
        ),
        "boot": ProfileSpec(
            slug="boot",
            domain="boot.spanel",
            image="httpd:alpine",
            web_root=APACHE_ROOT,
            files={"index.html": _boot_index()},
        ),
        "astro": ProfileSpec(
            slug="astro",
            domain="astro.spanel",
            image="node:24-alpine",
            web_root=ASTRO_ROOT,
            files={
                "package.json": _astro_package(),
                "astro.config.mjs": _astro_config("astro.spanel"),
                "src/pages/index.astro": _astro_index(),
            },
            command=(
                # El build necesita el volume sembrado completo. Espera al
                # ÚLTIMO archivo del seed (src/pages/index.astro): cuando
                # existe, package.json y astro.config.mjs ya están escritos
                # con la versión vigente. Salida de espera a los 300s.
                "i=0; while [ ! -f src/pages/index.astro ]; do "
                "i=$((i+1)); [ $i -gt 300 ] && exit 1; sleep 1; done; "
                "npm install --no-fund --no-audit "
                "&& npx astro build "
                "&& npx astro preview --host 0.0.0.0 --port 80"
            ),
            workdir=ASTRO_ROOT,
        ),
    }


def get_profiles(only: list[str] | None = None) -> dict[str, ProfileSpec]:
    all_profiles = build_profiles()
    if not only:
        return all_profiles
    unknown = [slug for slug in only if not VALID_SLUG.fullmatch(slug) or slug not in all_profiles]
    if unknown:
        raise ValueError(
            f"perfiles desconocidos: {', '.join(unknown)}; "
            f"válidos: {', '.join(all_profiles)}"
        )
    return {slug: all_profiles[slug] for slug in only}
