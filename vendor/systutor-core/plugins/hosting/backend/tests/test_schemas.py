"""A client cannot select a tenant or a container through a request body."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from plugins.hosting.backend.schemas import (
    HostingSiteSummary,
    ResetPasswordRequest,
    ResetPasswordResponse,
    WordPressUserSummary,
)


def test_reset_schema_acepta_password_opcional() -> None:
    assert ResetPasswordRequest().password is None
    assert ResetPasswordRequest(password="una-clave-larga").password == "una-clave-larga"


def test_reset_schema_rechaza_container_name() -> None:
    with pytest.raises(ValidationError, match="container"):
        ResetPasswordRequest.model_validate(
            {"password": "una-clave-larga", "container": "mailserver"}
        )


def test_reset_schema_rechaza_container_name_snake_case() -> None:
    with pytest.raises(ValidationError, match="container_name"):
        ResetPasswordRequest.model_validate(
            {"password": "una-clave-larga", "container_name": "mailserver"}
        )


def test_reset_schema_rechaza_tenant_id() -> None:
    with pytest.raises(ValidationError, match="tenant_id"):
        ResetPasswordRequest.model_validate(
            {"password": "una-clave-larga", "tenant_id": "otro-tenant"}
        )


def test_los_contratos_no_exponen_tenant_id() -> None:
    models = (
        ResetPasswordRequest,
        ResetPasswordResponse,
        HostingSiteSummary,
        WordPressUserSummary,
    )
    for model in models:
        assert "tenant_id" not in model.model_fields
        assert "container" not in model.model_fields
        assert "container_name" not in model.model_fields or model is HostingSiteSummary


def test_site_summary_muestra_el_contenedor_resuelto() -> None:
    site = HostingSiteSummary(
        id="site-1",
        container_name="spanel-demowp-wp",
        domain=None,
        status="running",
        managed=True,
    )
    assert site.container_name == "spanel-demowp-wp"
