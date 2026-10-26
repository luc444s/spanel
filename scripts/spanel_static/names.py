"""Nombres, dominios y etiquetas de un sitio estático.

Todo es puro: no toca Docker ni el sistema de archivos. Así los nombres se
pueden probar sin un daemon y el futuro plugin puede reutilizarlos tal cual.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

LABEL_STATIC = "com.spanel.static"
LABEL_SITE = "spanel.site"
LABEL_KIND = "spanel.kind"
LABEL_MANAGED = "spanel.managed"
LABEL_DOMAIN = "com.spanel.static.domain"
KIND = "static"
WEB_ROOT = "/usr/share/nginx/html"


def slugify(name: str) -> str:
    """Un slug estable de 3 a 40 caracteres, igual que el resto de SPanel."""
    plain = unicodedata.normalize("NFKD", (name or "")).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", plain.strip().lower()).strip("-")
    if not re.fullmatch(r"[a-z0-9-]{3,40}", slug):
        raise ValueError("El nombre debe producir un slug de 3 a 40 caracteres")
    return slug


def validate_domain(raw: str, profile: str = "internal") -> str:
    """Valida un FQDN sin esquema, puerto ni comodines.

    El perfil `internal` permite el TLD sintético `.spanel` del laboratorio; el
    perfil `public` exige un TLD con letras, como Let's Encrypt.
    """
    value = (raw or "").strip().lower().removesuffix(".")
    if not value:
        raise ValueError("El dominio no puede estar vacío")
    if any(token in value for token in ("/", ":", "*", " ", "\t")):
        raise ValueError("Dominio inválido: sin esquema, puerto, ruta ni comodines")
    labels = value.split(".")
    if len(labels) < 2:
        raise ValueError("Dominio inválido: hace falta al menos nombre y TLD")
    if any(not label for label in labels):
        raise ValueError("Dominio inválido: etiqueta vacía")
    for label in labels:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label):
            raise ValueError(f"Dominio inválido: etiqueta {label!r}")
    tld = labels[-1]
    if profile == "public":
        if tld == "spanel":
            raise ValueError("El TLD .spanel es solo del laboratorio; usá un dominio público")
        if not re.fullmatch(r"[a-z]{2,63}", tld):
            raise ValueError("Dominio público inválido: el TLD debe tener letras")
    return value


@dataclass(frozen=True, slots=True)
class SiteNames:
    """Los nombres derivados de un slug. No se guardan: se calculan."""

    slug: str

    @property
    def container(self) -> str:
        return f"spanel-{self.slug}-web"

    @property
    def volume(self) -> str:
        return f"spanel-{self.slug}-web"

    @property
    def service(self) -> str:
        return self.container

    @property
    def route_file(self) -> str:
        # `proxy_domain.slug_for` quita el prefijo `spanel-` pero conserva
        # `-web`, así que el archivo real es este. Se documenta para que no
        # sorprenda al buscarlo en el file provider.
        return f"spanel-{self.slug}-web.yml"

    def labels(self, domain: str | None = None) -> list[tuple[str, str]]:
        labels = [
            (LABEL_STATIC, "true"),
            (LABEL_SITE, self.slug),
            (LABEL_KIND, KIND),
            (LABEL_MANAGED, "true"),
        ]
        if domain:
            labels.append((LABEL_DOMAIN, domain))
        return labels

    def label_args(self, domain: str | None = None) -> list[str]:
        args: list[str] = []
        for key, value in self.labels(domain):
            args += ["--label", f"{key}={value}"]
        return args
