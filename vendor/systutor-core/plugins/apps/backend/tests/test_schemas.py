from __future__ import annotations

import pytest
from pydantic import ValidationError

from plugins.apps.backend.schemas import ApplicationCreate


def test_public_application_input_cannot_choose_runtime_identifiers() -> None:
    value = ApplicationCreate(
        name="Demo", repository="owner/repo", domain="demo.example",
        ingress_service="web", ingress_port=8080,
    )
    assert not hasattr(value, "runtime_project")
    with pytest.raises(ValidationError):
        ApplicationCreate(name="Demo", repository="https://github.com/owner/repo")
    with pytest.raises(ValidationError):
        ApplicationCreate(
            name="Demo", repository="owner/repo", domain="demo.example",
            ingress_service="web", ingress_port=65536,
        )


def test_compose_path_cannot_escape_checkout() -> None:
    with pytest.raises(ValidationError):
        ApplicationCreate(name="Demo", repository="owner/repo", compose_file="../compose.yml")
