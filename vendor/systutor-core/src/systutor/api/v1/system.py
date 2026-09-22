from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session, get_plugin_registry, get_settings_dep
from systutor.core.config import Settings
from systutor.core.database import check_database_connection
from systutor.core.lifecycle import ensure_session_factory
from systutor.kernel.auth.dependencies import (
    get_current_tenant_context,
    require_any_permission,
    require_permission,
)
from systutor.kernel.auth.models import User
from systutor.kernel.plugins.manifest import PluginManifest
from systutor.kernel.plugins.persistent import list_plugin_registry_records
from systutor.kernel.plugins.runtime import PluginManifestRegistry
from systutor.kernel.plugins.service import get_plugin_registry_record_by_plugin_id
from systutor.kernel.tenants.context import TenantContext

router = APIRouter(prefix="/system", tags=["system"])


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    env: str


class ReadyResponse(HealthResponse):
    plugins_loaded: int
    database_configured: bool
    database_connected: bool
    redis_configured: bool


class PluginRuntimeRecordResponse(BaseModel):
    id: str
    plugin_id: str
    name: str
    version: str
    api_version: str
    state: str
    is_enabled: bool
    backend_entrypoint: str | None
    frontend_entrypoint: str | None
    requires_json: list[str]
    permissions_json: list[str]
    events_json: list[str]
    description: str | None
    migration_version: str | None
    installed_at: datetime | None
    enabled_at: datetime | None
    disabled_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


def _health_payload(settings: Settings) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="api",
        version=settings.version,
        env=settings.env,
    )


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_settings_dep)) -> HealthResponse:
    return _health_payload(settings)


@router.get("/health/live", response_model=HealthResponse)
def live_alias(settings: Settings = Depends(get_settings_dep)) -> HealthResponse:
    return _health_payload(settings)


@router.get("/ready", response_model=ReadyResponse)
def ready(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    plugin_registry: PluginManifestRegistry = Depends(get_plugin_registry),
) -> ReadyResponse:
    database_connected = False

    try:
        database_connected = check_database_connection(ensure_session_factory(request.app))
    except Exception as exc:  # pragma: no cover - defensive path for runtime envs
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "error",
                "message": "database not ready",
                "reason": str(exc),
            },
        ) from exc

    return ReadyResponse(
        status="ok",
        service="api",
        version=settings.version,
        env=settings.env,
        plugins_loaded=len(plugin_registry.list()),
        database_configured=bool(settings.database_url),
        database_connected=database_connected,
        redis_configured=bool(settings.redis_url),
    )


@router.get("/health/ready", response_model=ReadyResponse)
def ready_alias(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    plugin_registry: PluginManifestRegistry = Depends(get_plugin_registry),
) -> ReadyResponse:
    return ready(request=request, settings=settings, plugin_registry=plugin_registry)


@router.get("/plugins", response_model=list[PluginManifest])
def list_plugins(
    _: User = Depends(require_permission("core.plugin.read")),
    plugin_registry: PluginManifestRegistry = Depends(get_plugin_registry),
) -> list[PluginManifest]:
    return plugin_registry.list()


@router.get("/plugin-runtime", response_model=list[PluginRuntimeRecordResponse])
def list_plugin_runtime(
    db: Session = Depends(get_db_session),
    _: User = Depends(
        require_any_permission(
            "core.plugin.read",
            "core.plugin.runtime.read",
            "core.plugin.manage",
        )
    ),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
) -> list[PluginRuntimeRecordResponse]:
    can_read_full_runtime = tenant_context.is_superadmin or any(
        permission in tenant_context.current_permissions
        for permission in ("core.plugin.runtime.read", "core.plugin.manage")
    )

    records = list_plugin_registry_records(db)
    if not can_read_full_runtime:
        records = [record for record in records if record.state == "enabled" and record.is_enabled]

    return [
        PluginRuntimeRecordResponse.model_validate(record, from_attributes=True)
        for record in records
    ]


@router.get("/plugin-runtime/{plugin_id}", response_model=PluginRuntimeRecordResponse)
def get_plugin_runtime(
    plugin_id: str,
    db: Session = Depends(get_db_session),
    _: User = Depends(require_any_permission("core.plugin.runtime.read", "core.plugin.manage")),
) -> PluginRuntimeRecordResponse:
    record = get_plugin_registry_record_by_plugin_id(db, plugin_id=plugin_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin runtime not found",
        )
    return PluginRuntimeRecordResponse.model_validate(record, from_attributes=True)
