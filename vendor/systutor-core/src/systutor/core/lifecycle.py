from __future__ import annotations

from contextlib import asynccontextmanager
from typing import cast

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker

from systutor.core.cache import close_cache, init_cache
from systutor.core.config import get_settings
from systutor.core.database import build_async_session_factory, build_session_factory
from systutor.core.logging import get_logger
from systutor.kernel.audit.service import record_audit
from systutor.kernel.events.bus import EventBus
from systutor.kernel.plugins.persistent import (
    build_persistent_plugin_runtime,
    get_plugin_registry_record_by_plugin_id,
)
from systutor.kernel.plugins.runtime import PluginManifestRegistry, PluginRuntime
from systutor.kernel.tasks.dispatcher import build_task_dispatcher
from systutor.sdk import PluginContext

logger = get_logger(__name__)


def bootstrap_app_state(app: FastAPI, settings=None) -> None:
    effective_settings = settings or get_settings()
    existing_session_factory = getattr(app.state, "session_factory", None)
    session_factory = existing_session_factory or build_session_factory(effective_settings)
    plugin_registry = PluginManifestRegistry(effective_settings.plugins_dir)
    plugin_registry.discover()
    event_bus = EventBus()
    task_dispatcher = build_task_dispatcher(effective_settings)

    def context_builder(manifest):
        return PluginContext(
            manifest,
            config=effective_settings,
            router_registry=app,
            event_bus=event_bus,
            audit_service=record_audit,
            db_session_provider=session_factory,
            task_dispatcher=task_dispatcher,
        )

    try:
        with session_factory() as db:
            plugin_runtime = build_persistent_plugin_runtime(
                db,
                registry=plugin_registry,
                context_builder=context_builder,
            )
            db.commit()
    except Exception as exc:  # pragma: no cover - fallback for environments without migrated DB
        logger.error(
            "plugin_runtime_persistence_unavailable",
            extra={
                "error": str(exc),
                "plugins_dir": str(effective_settings.plugins_dir),
            },
        )
        plugin_runtime = PluginRuntime(plugin_registry, context_builder=context_builder)
        plugin_runtime.load()

    for plugin_id, handlers in plugin_runtime.collect_event_handlers().items():
        event_bus.register_handlers(handlers, source=plugin_id)

    _mount_plugin_routes(app, plugin_runtime, effective_settings.api_prefix)

    app.state.settings = effective_settings
    app.state.plugin_registry = plugin_registry
    app.state.plugin_runtime = plugin_runtime
    app.state.event_bus = event_bus
    app.state.session_factory = session_factory
    app.state.async_session_factory = build_async_session_factory(effective_settings)
    app.state.task_dispatcher = task_dispatcher


def ensure_session_factory(app: FastAPI) -> sessionmaker[Session]:
    if app.state.session_factory is None:
        app.state.session_factory = build_session_factory(app.state.settings)
    return cast(sessionmaker[Session], app.state.session_factory)


def ensure_async_session_factory(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    factory = getattr(app.state, "async_session_factory", None)
    if factory is None:
        factory = build_async_session_factory(app.state.settings)
        app.state.async_session_factory = factory
    return cast(async_sessionmaker[AsyncSession], factory)


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap_app_state(app, getattr(app.state, "settings", None))
    settings = app.state.settings
    plugin_registry = app.state.plugin_registry

    init_cache(settings.redis_url)

    logger.info(
        "application_started",
        extra={
            "app_name": settings.app_name,
            "env": settings.env,
            "plugins_loaded": len(plugin_registry.list()),
            "plugins_enabled": len(
                [
                    item
                    for item in app.state.plugin_runtime.list_results()
                    if item.status == "enabled"
                ]
            ),
        },
    )
    yield
    close_cache()
    logger.info("application_stopped", extra={"app_name": settings.app_name, "env": settings.env})


def _mount_plugin_routes(app: FastAPI, plugin_runtime: PluginRuntime, api_prefix: str) -> None:
    plugin_prefix = f"{api_prefix}/plugins/"
    app.router.routes = [
        route
        for route in app.router.routes
        if not getattr(route, "path", "").startswith(plugin_prefix)
    ]

    for result in plugin_runtime.list_results():
        if result.status != "enabled" or result.registration is None:
            continue

        for router in result.registration.routers:
            app.include_router(
                cast(APIRouter, router),
                prefix=f"{api_prefix}/plugins/{result.plugin_id}",
                dependencies=[Depends(_require_enabled_plugin(result.plugin_id))],
            )


def _require_enabled_plugin(plugin_id: str):
    def dependency(request: Request) -> None:
        session_factory = ensure_session_factory(request.app)
        with session_factory() as db:
            record = get_plugin_registry_record_by_plugin_id(db, plugin_id=plugin_id)
            if record is None or record.state != "enabled" or not record.is_enabled:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Plugin route not available",
                )

    return dependency
