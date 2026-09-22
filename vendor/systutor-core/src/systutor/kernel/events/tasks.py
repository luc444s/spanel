from __future__ import annotations

import importlib

from systutor.core.config import get_settings
from systutor.core.database import build_session_factory
from systutor.kernel.audit.service import record_audit
from systutor.kernel.events.bus import EventBus, dispatch_pending_outbox_events
from systutor.kernel.plugins.persistent import build_persistent_plugin_runtime
from systutor.kernel.plugins.runtime import PluginManifestRegistry, PluginRuntime
from systutor.kernel.tasks.broker import configure_dramatiq_broker
from systutor.kernel.tasks.dispatcher import build_task_dispatcher
from systutor.sdk import PluginContext

settings = get_settings()
configure_dramatiq_broker(settings)


def build_runtime_event_bus() -> EventBus:
    session_factory = build_session_factory(settings)
    registry = PluginManifestRegistry(settings.plugins_dir)
    registry.discover()
    event_bus = EventBus()
    task_dispatcher = build_task_dispatcher(settings)

    def context_builder(manifest):
        return PluginContext(
            manifest,
            config=settings,
            router_registry=None,
            event_bus=event_bus,
            audit_service=record_audit,
            db_session_provider=session_factory,
            task_dispatcher=task_dispatcher,
        )

    try:
        with session_factory() as db:
            runtime = build_persistent_plugin_runtime(
                db,
                registry=registry,
                context_builder=context_builder,
            )
            db.commit()
    except Exception:  # pragma: no cover - same fallback semantics as app bootstrap
        runtime = PluginRuntime(registry, context_builder=context_builder)
        runtime.load()

    for plugin_id, handlers in runtime.collect_event_handlers().items():
        event_bus.register_handlers(handlers, source=plugin_id)

    return event_bus


def _dispatch_pending_events_impl() -> dict[str, int]:
    session_factory = build_session_factory(settings)
    event_bus = build_runtime_event_bus()
    with session_factory() as db:
        result = dispatch_pending_outbox_events(
            db,
            event_bus,
            limit=settings.outbox_dispatch_batch_size,
            max_retries=settings.outbox_max_retries,
        )
        db.commit()
        return result


dramatiq = importlib.import_module("dramatiq")
dispatch_pending_events = dramatiq.actor(queue_name="events")(_dispatch_pending_events_impl)
