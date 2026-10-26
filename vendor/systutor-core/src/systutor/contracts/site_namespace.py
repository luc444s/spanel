"""Contrato de namespace de sitios por tenant.

El sufijo de sitios es configuración del tenant, separada del dominio de
correo (`tenant.domain`). El plugin `tenant` es el dueño del dato y registra un
provider; el plugin `domains` consume el contrato sin importar el servicio de
`tenant`.

Las funciones de normalización son puras: no tocan la base ni la app. Los
errores de formato se reportan con `ValueError` para que la capa que llama
decida el código HTTP (`422` en la API).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from fastapi import FastAPI
from sqlalchemy.orm import Session

SiteSuffixProvider = Callable[[Session, str], str | None]

# Una etiqueta DNS: letras, dígitos y guiones internos. Sin comodines.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_SUFFIX_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*$")
_FORBIDDEN_RE = re.compile(r"[\s/:*?#@]")


def normalize_site_suffix(value: str | None) -> str | None:
    """Normaliza un sufijo de sitios o devuelve `None` si está vacío.

    Minúsculas, sin punto final, sin esquema, puerto, ruta ni comodines. Un
    sufijo de una etiqueta (`spanel`) es válido, igual que uno de varias
    (`spanel.site`).
    """
    if value is None:
        return None
    text = value.strip().lower().removesuffix(".")
    if not text:
        return None
    if _FORBIDDEN_RE.search(text) or _SUFFIX_RE.fullmatch(text) is None:
        raise ValueError(
            "sufijo de sitios inválido: sin esquema, puerto, ruta, espacios ni comodines"
        )
    return text


def default_suffix_from_domain(domain: str | None) -> str | None:
    """Sufijo inicial cuando el tenant todavía no configuró uno."""
    return normalize_site_suffix(domain)


def domain_in_suffix(domain: str, suffix: str | None) -> bool:
    """¿`domain` es el sufijo o está debajo de él?

    La comparación es por etiquetas: `paginadelucas.spanel` pertenece a
    `spanel`, pero `paginadelucas.spanel.site` no. El dominio no se normaliza
    con validación estricta para que un dato histórico raro no rompa el
    listado.
    """
    if not suffix:
        return False
    try:
        normalized_suffix = normalize_site_suffix(suffix)
    except ValueError:
        return False
    if not normalized_suffix:
        return False
    needle = (domain or "").strip().lower().removesuffix(".")
    if not needle:
        return False
    return needle == normalized_suffix or needle.endswith(f".{normalized_suffix}")


def site_suffixes_overlap(first: str, second: str) -> bool:
    """¿Dos sufijos configurados se pisan?

    Además de la igualdad, `spanel` y `spanel.site` se consideran solapados:
    el sufijo más específico empieza por el más corto. Es la regla de negocio
    del namespace, no una simple comparación de subdominios.
    """
    try:
        left = normalize_site_suffix(first)
        right = normalize_site_suffix(second)
    except ValueError:
        return False
    if not left or not right:
        return False
    return (
        left == right
        or left.startswith(f"{right}.")
        or right.startswith(f"{left}.")
        or left.endswith(f".{right}")
        or right.endswith(f".{left}")
    )


def register_site_suffix_provider(
    app: FastAPI,
    name: str,
    provider: SiteSuffixProvider,
) -> None:
    providers: dict[str, SiteSuffixProvider] | None = getattr(
        app.state, "site_suffix_providers", None
    )
    if providers is None:
        providers = {}
        app.state.site_suffix_providers = providers
    existing = providers.get(name)
    if existing is not None and (
        existing.__module__, existing.__qualname__
    ) != (provider.__module__, provider.__qualname__):
        raise ValueError(f"Duplicate site suffix provider: {name}")
    providers[name] = provider


def get_site_suffix(app: FastAPI, db: Session, tenant_id: str) -> str | None:
    """Sufijo de sitios configurado para un tenant, si algún provider lo sabe."""
    providers: dict[str, SiteSuffixProvider] = getattr(
        app.state, "site_suffix_providers", {}
    )
    for provider in providers.values():
        value = provider(db, tenant_id)
        if value is None:
            continue
        try:
            return normalize_site_suffix(value)
        except ValueError:
            return None
    return None
