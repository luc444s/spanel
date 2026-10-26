"""Contrato puro del namespace de sitios (A.SPEC 0030 / V1)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from systutor.contracts.site_namespace import (
    default_suffix_from_domain,
    domain_in_suffix,
    get_site_suffix,
    normalize_site_suffix,
    register_site_suffix_provider,
    site_suffixes_overlap,
)


def test_normaliza_espacios_mayusculas_y_punto_final() -> None:
    assert normalize_site_suffix(" Spanel.SITE. ") == "spanel.site"


def test_vacio_y_none_son_ausencia_de_sufijo() -> None:
    assert normalize_site_suffix(None) is None
    assert normalize_site_suffix("") is None
    assert normalize_site_suffix("   ") is None


@pytest.mark.parametrize(
    "value",
    ["mal sufijo", "mal/sufijo", "mal:sufijo", "*.spanel", "http://spanel"],
)
def test_rechaza_formas_invalidas(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_site_suffix(value)


def test_permite_una_o_varias_etiquetas() -> None:
    assert normalize_site_suffix("spanel") == "spanel"
    assert normalize_site_suffix("spanel.site") == "spanel.site"


def test_domain_in_suffix_compara_por_etiquetas() -> None:
    assert domain_in_suffix("paginadelucas.spanel", "spanel") is True
    assert domain_in_suffix("paginadelucas.spanel.site", "spanel") is False
    assert domain_in_suffix("spanel", "spanel") is True
    assert domain_in_suffix("paginadelucas.spanel", "spanel.site") is False
    assert domain_in_suffix("paginadelucas.spanel", None) is False


def test_sufijos_solapados_por_prefijo_de_etiquetas() -> None:
    assert site_suffixes_overlap("spanel", "spanel") is True
    assert site_suffixes_overlap("spanel", "spanel.site") is True
    assert site_suffixes_overlap("spanel.site", "spanel") is True
    assert site_suffixes_overlap("dos.spanel", "spanel") is True
    assert site_suffixes_overlap("spanel.site", "otro.site") is False


def test_default_suffix_deriva_del_dominio() -> None:
    assert default_suffix_from_domain("spanel.site") == "spanel.site"
    assert default_suffix_from_domain(" Spanel.SITE. ") == "spanel.site"
    assert default_suffix_from_domain(None) is None


def test_registry_entrega_el_sufijo_del_provider() -> None:
    app = FastAPI()
    register_site_suffix_provider(app, "tenant", lambda db, tenant_id: "spanel.site")
    assert get_site_suffix(app, None, "t1") == "spanel.site"  # type: ignore[arg-type]


def test_registry_es_idempotente_y_rechaza_otra_implementacion() -> None:
    app = FastAPI()

    def provider(db, tenant_id):
        return None

    def other(db, tenant_id):
        return None

    register_site_suffix_provider(app, "tenant", provider)
    register_site_suffix_provider(app, "tenant", provider)
    with pytest.raises(ValueError):
        register_site_suffix_provider(app, "tenant", other)


def test_registry_sin_provider_ni_sufijo_devuelve_none() -> None:
    app = FastAPI()
    assert get_site_suffix(app, None, "t1") is None  # type: ignore[arg-type]
