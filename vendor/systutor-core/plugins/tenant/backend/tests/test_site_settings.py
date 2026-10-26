"""Servicio del sufijo de sitios por tenant (A.SPEC 0030 / V2)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401 - registra las tablas del kernel
from plugins.tenant.backend.models import TenantSiteSettings
from plugins.tenant.backend.service import (
    create_tenant,
    get_site_suffix_for_tenant,
    get_tenant_by_id,
    set_site_suffix,
    update_tenant,
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


def _settings(db: Session, tenant_id: str) -> TenantSiteSettings:
    settings = db.scalar(
        select(TenantSiteSettings).where(TenantSiteSettings.tenant_id == tenant_id)
    )
    assert settings is not None
    return settings


def test_crear_tenant_deriva_el_sufijo_del_dominio(db: Session) -> None:
    tenant = create_tenant(db, name="Demo", slug="demo", domain="demo.com")
    db.commit()

    assert tenant.domain == "demo.com"
    assert _settings(db, tenant.id).site_suffix == "demo.com"
    assert get_site_suffix_for_tenant(db, tenant.id) == "demo.com"


def test_crear_tenant_sin_dominio_deja_sufijo_null(db: Session) -> None:
    tenant = create_tenant(db, name="Demo", slug="demo", domain=None)
    db.commit()

    assert tenant.domain is None
    assert _settings(db, tenant.id).site_suffix is None


def test_crear_tenant_sin_dominio_normaliza_vacio(db: Session) -> None:
    tenant = create_tenant(db, name="Demo", slug="demo", domain="  ")
    db.commit()

    assert _settings(db, tenant.id).site_suffix is None


def test_actualizar_solo_sufijo_conserva_dominio(db: Session) -> None:
    tenant = create_tenant(db, name="Demo", slug="demo", domain="demo.com")
    db.commit()

    updated = update_tenant(db, tenant=tenant, site_suffix="spanel")
    db.commit()

    assert updated.domain == "demo.com"
    assert _settings(db, tenant.id).site_suffix == "spanel"


def test_actualizar_solo_dominio_conserva_sufijo(db: Session) -> None:
    tenant = create_tenant(db, name="Demo", slug="demo", domain="demo.com")
    set_site_suffix(db, tenant_id=tenant.id, site_suffix="spanel")
    db.commit()

    updated = update_tenant(db, tenant=tenant, domain="otro.com")
    db.commit()

    assert updated.domain == "otro.com"
    assert _settings(db, tenant.id).site_suffix == "spanel"


def test_actualizar_ambos_campos(db: Session) -> None:
    tenant = create_tenant(db, name="Demo", slug="demo", domain="demo.com")
    db.commit()

    updated = update_tenant(db, tenant=tenant, domain="otro.com", site_suffix="otro.site")
    db.commit()

    assert updated.domain == "otro.com"
    assert _settings(db, tenant.id).site_suffix == "otro.site"


def test_limpiar_sufijo_con_string_vacio(db: Session) -> None:
    tenant = create_tenant(db, name="Demo", slug="demo", domain="demo.com")
    db.commit()

    update_tenant(db, tenant=tenant, site_suffix="")
    db.commit()

    assert _settings(db, tenant.id).site_suffix is None


def test_sufijo_invalido_es_422_y_no_escribe(db: Session) -> None:
    tenant = create_tenant(db, name="Demo", slug="demo", domain="demo.com")
    db.commit()

    with pytest.raises(AppError) as exc:
        update_tenant(db, tenant=tenant, site_suffix="mal sufijo")

    assert exc.value.status_code == 422
    assert _settings(db, tenant.id).site_suffix == "demo.com"


def _two_tenants(db: Session) -> tuple[Tenant, Tenant]:
    first = Tenant(name="Uno", slug="uno", domain="uno.com")
    second = Tenant(name="Dos", slug="dos", domain="dos.com")
    db.add_all([first, second])
    db.commit()
    return first, second


def test_sufijos_iguales_devuelven_409(db: Session) -> None:
    first, second = _two_tenants(db)
    set_site_suffix(db, tenant_id=first.id, site_suffix="spanel")
    db.commit()

    with pytest.raises(AppError) as exc:
        set_site_suffix(db, tenant_id=second.id, site_suffix="spanel")

    assert exc.value.status_code == 409
    assert exc.value.code == "tenant_site_suffix_conflict"
    assert "Uno" in exc.value.message


@pytest.mark.parametrize(("first_suffix", "second_suffix"), [("spanel", "spanel.site"), ("spanel.site", "spanel")])
def test_sufijos_solapados_devuelven_409(
    db: Session, first_suffix: str, second_suffix: str
) -> None:
    first, second = _two_tenants(db)
    set_site_suffix(db, tenant_id=first.id, site_suffix=first_suffix)
    db.commit()

    with pytest.raises(AppError) as exc:
        update_tenant(db, tenant=second, site_suffix=second_suffix)

    assert exc.value.status_code == 409
    assert exc.value.code == "tenant_site_suffix_conflict"
    assert "Uno" in exc.value.message
    assert get_site_suffix_for_tenant(db, second.id) is None


def test_crear_tenant_con_dominio_solapado_devuelve_409(db: Session) -> None:
    create_tenant(db, name="Uno", slug="uno", domain="uno.com")
    set_site_suffix(
        db,
        tenant_id=_tenant(db, "uno").id,
        site_suffix="spanel",
    )
    db.commit()

    with pytest.raises(AppError) as exc:
        create_tenant(db, name="Dos", slug="dos", domain="dos.spanel")

    assert exc.value.status_code == 409
    assert exc.value.code == "tenant_site_suffix_conflict"


def test_el_mismo_tenant_puede_conservar_su_sufijo(db: Session) -> None:
    tenant = create_tenant(db, name="Demo", slug="demo", domain="demo.com")
    db.commit()

    updated = update_tenant(db, tenant=tenant, site_suffix="DEMO.COM.")
    db.commit()

    assert updated.domain == "demo.com"
    assert get_tenant_by_id(db, tenant.id) is not None
    assert get_site_suffix_for_tenant(db, tenant.id) == "demo.com"


def test_el_mismo_tenant_puede_limpiar_su_dominio_y_conservar_sufijo(db: Session) -> None:
    tenant = create_tenant(db, name="Demo", slug="demo", domain="demo.com")
    db.commit()

    update_tenant(db, tenant=tenant, domain=None)
    db.commit()

    assert tenant.domain is None
    assert get_site_suffix_for_tenant(db, tenant.id) == "demo.com"
