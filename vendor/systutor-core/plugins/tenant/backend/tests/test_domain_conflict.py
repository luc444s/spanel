"""El dominio del tenant es único y un choque debe ser 409, nunca 500."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401 - registra las tablas del kernel
from plugins.tenant.backend.service import (
    ensure_domain_available,
    normalize_tenant_domain,
    update_tenant_domain,
)
from systutor.core.database import Base
from systutor.core.errors import AppError
from systutor.kernel.tenants.models import Tenant


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def _tenant(db: Session, slug: str) -> Tenant:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    assert tenant is not None
    return tenant


def test_normaliza_minusculas_punto_final_y_vacio(db: Session) -> None:
    assert normalize_tenant_domain("  Empresa.COM. ") == "empresa.com"
    assert normalize_tenant_domain("") is None
    assert normalize_tenant_domain("   ") is None
    assert normalize_tenant_domain(None) is None


def test_rechaza_espacios_y_barras() -> None:
    with pytest.raises(AppError) as exc:
        normalize_tenant_domain("mal dominio")
    assert exc.value.status_code == 422
    with pytest.raises(AppError):
        normalize_tenant_domain("mal/dominio")


def test_dominio_duplicado_devuelve_409(db: Session) -> None:
    db.add_all(
        [
            Tenant(name="Uno", slug="uno", domain="uno.com"),
            Tenant(name="Dos", slug="dos", domain="dos.com"),
        ]
    )
    db.commit()

    with pytest.raises(AppError) as exc:
        update_tenant_domain(db, tenant=_tenant(db, "dos"), domain="uno.com")

    assert exc.value.status_code == 409
    assert exc.value.code == "tenant_domain_conflict"
    assert "Uno" in exc.value.message
    assert _tenant(db, "dos").domain == "dos.com"


def test_duplicado_ignora_mayusculas(db: Session) -> None:
    db.add_all(
        [
            Tenant(name="Uno", slug="uno", domain="uno.com"),
            Tenant(name="Dos", slug="dos", domain="dos.com"),
        ]
    )
    db.commit()

    with pytest.raises(AppError):
        ensure_domain_available(db, domain="UNO.com")


def test_el_mismo_tenant_puede_conservar_su_dominio(db: Session) -> None:
    db.add(Tenant(name="Uno", slug="uno", domain="uno.com"))
    db.commit()

    updated = update_tenant_domain(db, tenant=_tenant(db, "uno"), domain="UNO.com.")
    assert updated.domain == "uno.com"


def test_dominio_vacio_queda_en_null(db: Session) -> None:
    db.add(Tenant(name="Uno", slug="uno", domain="uno.com"))
    db.commit()

    updated = update_tenant_domain(db, tenant=_tenant(db, "uno"), domain="")
    assert updated.domain is None
