"""Contenido inicial de un sitio estático.

Los archivos viven en `templates/` para que se puedan editar sin tocar Python.
El marcador `spanel-static` permite saber si el contenido lo puso este CLI y no
sobrescribir el trabajo de una persona sin `--force`.
"""

from __future__ import annotations

from pathlib import Path

from .docker import Docker
from .names import WEB_ROOT

TEMPLATES = Path(__file__).resolve().parent / "templates"
MARKER = "spanel-static"
FILES = ("index.html", "styles.css", "app.js")


def template_path(name: str) -> Path:
    path = TEMPLATES / name
    if not path.is_file():
        raise FileNotFoundError(f"falta la plantilla {path}")
    return path


def read_template(name: str) -> str:
    return template_path(name).read_text(encoding="utf-8")


def seed_content(docker: Docker, container: str, *, force: bool = False) -> list[str]:
    """Escribe las plantillas que falten. Devuelve los nombres escritos.

    `nginx:alpine` trae un `index.html` de bienvenida y Docker lo copia al
    volumen vacío. Por eso no alcanza con `test -e`: si el archivo es el de
    nginx, se reemplaza; si es contenido propio (marcador ausente), se respeta
    salvo `--force`.
    """
    docker.exec_in(container, 'mkdir -p "$1"', WEB_ROOT + "/assets")
    written: list[str] = []
    for name in FILES:
        destination = f"{WEB_ROOT}/{name}"
        existing = None if force else docker.read_file(container, destination)
        if existing is not None:
            if name == "index.html":
                if MARKER in existing or "Welcome to nginx!" not in existing:
                    continue
            else:
                continue
        docker.write_file(container, destination, read_template(name))
        written.append(name)
    docker.chown(container)
    return written
