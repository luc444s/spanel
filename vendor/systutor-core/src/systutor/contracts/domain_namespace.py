"""Contrato de propiedad explícita de dominios por tenant.

La propiedad de un dominio es un dato propio del tenant y no se deduce de su
sufijo de sitios. El plugin `tenant` es el dueño del dato y registra un
provider; `domains`, `hosting` y `mail` consumen el contrato sin importar
modelos de otro plugin.

Las funciones de normalización son puras: no tocan la base ni la app. Los
errores de formato se reportan con `ValueError` para que la capa que llama
decida el código HTTP (`422` en la API).

Este contrato es de solo lectura para los consumidores. Los plugins que
necesitan reclamar un dominio libre lo hacen con `claim_domain`, que delega en
el provider del dueño del dato en lugar de escribir su tabla.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from fastapi import FastAPI
from sqlalchemy.orm import Session

DomainListProvider = Callable[[Session, str], Sequence[str]]
DomainOwnerProvider = Callable[[Session, str], "str | None"]
DomainClaimProvider = Callable[[Session, str, str], None]
DomainUsageProvider = Callable[[Session, str], "DomainUsage | None"]


@dataclass(frozen=True)
class DomainUsage:
    """Uso publicado de un dominio que no vive en `tenant_domains`.

    `kind="primary"` es el dominio primario de un sitio; `kind="extra"` es una
    ruta extra del proxy. `tenant_id` es el dueño del contenedor cuando se
    conoce.
    """

    container: str
    kind: str
    tenant_id: str | None = None


class DomainConflictError(ValueError):
    """El dominio ya pertenece a otro tenant."""

    def __init__(self, domain: str, owner: str) -> None:
        super().__init__(f"El dominio {domain} pertenece al tenant {owner}")
        self.domain = domain
        self.owner = owner


# Una etiqueta DNS: letras, dígitos y guiones internos. Sin comodines.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*$")
_FORBIDDEN_RE = re.compile(r"[\s/:*?#@]")


def normalize_domain_name(value: str | None) -> str:
    """Normaliza un dominio o lanza `ValueError` si no es válido.

    Minúsculas, sin punto final, sin esquema, puerto, ruta, espacios ni
    comodines. Acepta ápices (`debug.com`) y subdominios
    (`mail.spanel.site`).
    """
    if value is None:
        raise ValueError("dominio inválido: no puede estar vacío")
    text = value.strip().lower().removesuffix(".")
    if not text:
        raise ValueError("dominio inválido: no puede estar vacío")
    if len(text) > 253 or _FORBIDDEN_RE.search(text) or _DOMAIN_RE.fullmatch(text) is None:
        raise ValueError(
            "dominio inválido: sin esquema, puerto, ruta, espacios ni comodines"
        )
    return text


def _providers(app: FastAPI | None, attribute: str) -> dict:
    if app is None:
        return {}
    return getattr(app.state, attribute, {}) or {}


def _register(app: FastAPI, attribute: str, name: str, provider: object) -> None:
    providers: dict | None = getattr(app.state, attribute, None)
    if providers is None:
        providers = {}
        setattr(app.state, attribute, providers)
    existing = providers.get(name)
    if existing is not None and (
        getattr(existing, "__module__", None),
        getattr(existing, "__qualname__", None),
    ) != (
        getattr(provider, "__module__", None),
        getattr(provider, "__qualname__", None),
    ):
        raise ValueError(f"Duplicate domain provider: {name}")
    providers[name] = provider


def register_domain_providers(
    app: FastAPI,
    name: str,
    *,
    list_domains: DomainListProvider,
    owner: DomainOwnerProvider,
    claim: DomainClaimProvider | None = None,
) -> None:
    """Registra quién sabe qué dominios posee un tenant y quién los reclama.

    `list_domains` devuelve los dominios propios del tenant; `owner` devuelve
    el tenant dueño de un dominio o `None` si está libre. `claim` es opcional
    y debe ser idempotente para el mismo tenant.
    """
    candidate = {
        "list_domains": list_domains,
        "owner": owner,
        "claim": claim,
    }
    providers: dict | None = getattr(app.state, "domain_namespace_providers", None)
    if providers is None:
        providers = {}
        app.state.domain_namespace_providers = providers
    existing = providers.get(name)
    if existing is not None and not all(
        _same_callable(existing.get(key), value) for key, value in candidate.items()
    ):
        raise ValueError(f"Duplicate domain provider: {name}")
    providers[name] = candidate


def _same_callable(first: object, second: object) -> bool:
    """Compara callables por módulo y nombre.

    El runtime carga cada plugin con `module_from_spec`, así que una segunda
    carga del mismo plugin produce objetos función nuevos con el mismo
    `__module__`/`__qualname__`. Identidad estricta rompería ese caso.
    """
    if first is second:
        return True
    if first is None or second is None:
        return False
    return (
        getattr(first, "__module__", None),
        getattr(first, "__qualname__", None),
    ) == (
        getattr(second, "__module__", None),
        getattr(second, "__qualname__", None),
    )


def register_domain_usage(app: FastAPI, name: str, usage: DomainUsageProvider) -> None:
    """Registra quién puede describir el uso publicado de un dominio."""
    _register(app, "domain_usage_providers", name, usage)


def list_tenant_domains(app: FastAPI | None, db: Session, tenant_id: str) -> set[str]:
    """Conjunto normalizado de dominios propiedad del tenant."""
    domains: set[str] = set()
    for provider in _providers(app, "domain_namespace_providers").values():
        for raw in provider["list_domains"](db, tenant_id):
            try:
                domains.add(normalize_domain_name(raw))
            except ValueError:
                # Un dato histórico inválido nunca rompe una lectura.
                continue
    return domains


def domain_owner(app: FastAPI | None, db: Session, domain: str) -> str | None:
    """Tenant dueño del dominio, o `None` si está libre o es inválido."""
    try:
        needle = normalize_domain_name(domain)
    except ValueError:
        return None
    for provider in _providers(app, "domain_namespace_providers").values():
        owner = provider["owner"](db, needle)
        if owner:
            return owner
    return None


def domain_in_namespace(
    app: FastAPI | None, db: Session, tenant_id: str, domain: str
) -> bool:
    """¿El dominio pertenece explícitamente a este tenant?"""
    return domain_owner(app, db, domain) == tenant_id


def claim_domain(app: FastAPI | None, db: Session, tenant_id: str, domain: str) -> bool:
    """Reclama un dominio libre para el tenant.

    Devuelve `True` si algún provider lo reclamó; `False` cuando no hay
    provider registrado o ninguno implementa la escritura. Un dominio de otro
    tenant lanza `DomainConflictError`.
    """
    needle = normalize_domain_name(domain)
    for provider in _providers(app, "domain_namespace_providers").values():
        claim = provider.get("claim")
        if claim is None:
            continue
        claim(db, tenant_id, needle)
        return True
    return False


def domain_usage(app: FastAPI | None, db: Session, domain: str) -> DomainUsage | None:
    """Uso publicado del dominio (primario o extra), si algún provider lo sabe."""
    try:
        needle = normalize_domain_name(domain)
    except ValueError:
        return None
    for provider in _providers(app, "domain_usage_providers").values():
        usage = provider(db, needle)
        if usage is not None:
            return usage
    return None
