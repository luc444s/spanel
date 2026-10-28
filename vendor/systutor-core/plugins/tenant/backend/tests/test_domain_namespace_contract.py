"""Contrato puro de propiedad de dominios (A.SPEC 0037 / V1)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from systutor.contracts.domain_namespace import (
    DomainConflictError,
    domain_in_namespace,
    domain_owner,
    list_tenant_domains,
    normalize_domain_name,
    register_domain_providers,
)


def test_normaliza_mayusculas_punto_espacios_y_apices() -> None:
    assert normalize_domain_name(" Debug.COM. ") == "debug.com"
    assert normalize_domain_name("mail.spanel.site") == "mail.spanel.site"
    assert normalize_domain_name("debug.com") == "debug.com"


@pytest.mark.parametrize(
    "value",
    ["https://x", "x:443", "*.x", "x y", "", "   ", None],
)
def test_rechaza_invalidos(value: str | None) -> None:
    with pytest.raises(ValueError):
        normalize_domain_name(value)


def test_sin_providers_no_hay_propiedad() -> None:
    app = FastAPI()

    assert list_tenant_domains(app, None, "t1") == set()  # type: ignore[arg-type]
    assert domain_owner(app, None, "debug.com") is None  # type: ignore[arg-type]


def test_owner_y_namespace_por_provider() -> None:
    app = FastAPI()
    ownership = {"t1": {"debug.com"}, "t2": {"test2.com"}}

    def owner(db, domain):
        for tenant_id, names in ownership.items():
            if domain in names:
                return tenant_id
        return None

    register_domain_providers(
        app,
        "tenant",
        list_domains=lambda db, tenant_id: ownership.get(tenant_id, set()),
        owner=owner,
    )

    assert list_tenant_domains(app, None, "t1") == {"debug.com"}  # type: ignore[arg-type]
    assert domain_in_namespace(app, None, "t1", "debug.com") is True  # type: ignore[arg-type]
    assert domain_in_namespace(app, None, "t1", "test2.com") is False  # type: ignore[arg-type]
    assert domain_in_namespace(app, None, "t2", "test2.com") is True  # type: ignore[arg-type]


def test_registry_idempotente_y_rechaza_otra_implementacion() -> None:
    app = FastAPI()

    def list_a(db, tenant_id):
        return []

    def owner_a(db, domain):
        return None

    register_domain_providers(app, "tenant", list_domains=list_a, owner=owner_a)
    register_domain_providers(app, "tenant", list_domains=list_a, owner=owner_a)
    with pytest.raises(ValueError):
        register_domain_providers(
            app,
            "tenant",
            list_domains=lambda db, tenant_id: [],
            owner=owner_a,
        )


def test_reload_del_mismo_provider_es_idempotente() -> None:
    """El runtime recarga plugins con funciones nuevas del mismo módulo."""
    app = FastAPI()

    def list_a(db, tenant_id):
        return []

    def owner_a(db, domain):
        return None

    register_domain_providers(app, "tenant", list_domains=list_a, owner=owner_a)

    def list_a_reloaded(db, tenant_id):  # mismo modulo/qualname que list_a
        return []

    def owner_a_reloaded(db, domain):
        return None

    list_a_reloaded.__qualname__ = list_a.__qualname__
    owner_a_reloaded.__qualname__ = owner_a.__qualname__

    register_domain_providers(
        app, "tenant", list_domains=list_a_reloaded, owner=owner_a_reloaded
    )


def test_claim_conflictivo_no_cambia_el_dueno() -> None:
    app = FastAPI()

    def claim(db, tenant_id, domain):
        raise DomainConflictError(domain, "t2")

    register_domain_providers(
        app,
        "tenant",
        list_domains=lambda db, tenant_id: [],
        owner=lambda db, domain: None,
        claim=claim,
    )

    from systutor.contracts.domain_namespace import claim_domain

    with pytest.raises(DomainConflictError):
        claim_domain(app, None, "t1", "debug.com")  # type: ignore[arg-type]
