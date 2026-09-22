from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from systutor.contracts.events import EventContract
from systutor.kernel.audit.models import AuditLog
from systutor.kernel.events.bus import EventBus, dispatch_pending_outbox_events
from systutor.kernel.events.models import EventLog, EventOutbox
from systutor.kernel.plugins.manifest import PluginManifest
from systutor.kernel.plugins.runtime import PluginManifestRegistry, PluginRuntime
from systutor.sdk import PluginRegistration


def _write_plugin(plugin_root: Path, *, plugin_id: str, requires: list[str] | None = None) -> None:
    plugin_root.mkdir(parents=True, exist_ok=True)
    (plugin_root / "backend").mkdir(exist_ok=True)
    (plugin_root / "frontend").mkdir(exist_ok=True)
    (plugin_root / "migrations").mkdir(exist_ok=True)
    (plugin_root / "permissions").mkdir(exist_ok=True)
    (plugin_root / "events").mkdir(exist_ok=True)
    manifest = {
        "id": plugin_id,
        "name": plugin_id.title(),
        "version": "0.1.0",
        "api_version": "1",
        "requires": requires or [],
        "backend_entrypoint": "backend.plugin:register",
        "frontend_entrypoint": "frontend/register.ts",
        "permissions": [f"{plugin_id}.sample.read"],
        "events": [f"{plugin_id}.sample.created"],
        "description": f"Plugin {plugin_id}",
    }
    (plugin_root / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin_root / "frontend" / "register.ts").write_text(
        "export function registerPlugin() { return {}; }\n",
        encoding="utf-8",
    )
    (plugin_root / "README.md").write_text(f"# {plugin_id}\n", encoding="utf-8")
    (plugin_root / "backend" / "plugin.py").write_text(
        "from systutor.sdk import PluginContext\n\n"
        "def register(context: PluginContext) -> None:\n"
        f"    context.register_permissions(['{plugin_id}.sample.read'])\n"
        f"    context.register_events(['{plugin_id}.sample.created'])\n",
        encoding="utf-8",
    )


def test_event_bus_emits_event_and_persists_outbox(
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    bus = EventBus()

    event_log = bus.publish(
        db_session,
        event=EventContract(
            event_name="core.runtime.emitted",
            module="core",
            tenant_id=seeded_demo["tenant_id"],
            branch_id=seeded_demo["branch_id"],
            actor_user_id=seeded_demo["user_id"],
            actor_type="user",
            entity_type="runtime",
            entity_id="evt-1",
            correlation_id="corr-runtime-1",
            payload={"source": "pytest"},
            metadata={"request_id": "req-runtime-1"},
        ),
    )

    outbox = db_session.scalar(select(EventOutbox).where(EventOutbox.event_log_id == event_log.id))

    assert event_log.event_name == "core.runtime.emitted"
    assert event_log.correlation_id == "corr-runtime-1"
    assert outbox is not None
    assert outbox.status == "pending"
    assert outbox.retry_count == 0


def test_dispatcher_executes_listener_and_marks_outbox_processed(
    db_session: Session, seeded_demo: dict[str, str]
) -> None:
    bus = EventBus()
    seen: list[str] = []

    def listener(event: EventContract) -> None:
        seen.append(event.event_name)

    bus.register_listener("core.runtime.processed", listener, source="tests")
    bus.publish(
        db_session,
        event=EventContract(
            event_name="core.runtime.processed",
            module="core",
            tenant_id=seeded_demo["tenant_id"],
            branch_id=seeded_demo["branch_id"],
            actor_user_id=seeded_demo["user_id"],
            actor_type="user",
            entity_type="runtime",
            entity_id="evt-2",
            payload={},
            metadata={},
        ),
    )

    result = dispatch_pending_outbox_events(db_session, bus, limit=10, max_retries=2)
    outbox = db_session.scalar(
        select(EventOutbox).where(EventOutbox.event_name == "core.runtime.processed")
    )

    assert result == {"processed": 1, "failed": 0, "total": 1}
    assert seen == ["core.runtime.processed"]
    assert outbox is not None
    assert outbox.status == "processed"
    assert outbox.processed_at is not None


def test_dispatcher_marks_outbox_failed_and_audits_listener_error(
    db_session: Session, seeded_demo: dict[str, str]
) -> None:
    bus = EventBus()

    def broken_listener(_: EventContract) -> None:
        raise RuntimeError("listener exploded")

    bus.register_listener("core.runtime.failed", broken_listener, source="tests")
    bus.publish(
        db_session,
        event=EventContract(
            event_name="core.runtime.failed",
            module="core",
            tenant_id=seeded_demo["tenant_id"],
            branch_id=seeded_demo["branch_id"],
            actor_user_id=seeded_demo["user_id"],
            actor_type="user",
            entity_type="runtime",
            entity_id="evt-3",
            correlation_id="corr-runtime-3",
            payload={},
            metadata={"request_id": "req-runtime-3"},
        ),
    )

    result = dispatch_pending_outbox_events(db_session, bus, limit=10, max_retries=2)
    outbox = db_session.scalar(
        select(EventOutbox).where(EventOutbox.event_name == "core.runtime.failed")
    )
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "event.listener"))

    assert result == {"processed": 0, "failed": 1, "total": 1}
    assert outbox is not None
    assert outbox.status == "failed"
    assert outbox.retry_count == 1
    assert outbox.error_message == "listener exploded"
    assert audit is not None
    assert audit.correlation_id == "corr-runtime-3"
    assert audit.result == "failure"
    assert audit.details["event_name"] == "core.runtime.failed"


def test_plugin_manifest_validation_rejects_invalid_permission() -> None:
    with pytest.raises(ValueError, match="plugin id namespace"):
        PluginManifest.model_validate(
            {
                "id": "broken",
                "name": "Broken",
                "version": "0.1.0",
                "api_version": "1",
                "requires": [],
                "backend_entrypoint": "backend.plugin:register",
                "frontend_entrypoint": "frontend/register.ts",
                "permissions": ["other.sample.read"],
                "events": [],
                "description": "Broken plugin",
            }
        )


def test_plugin_runtime_marks_missing_dependency_as_failed(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir / "billing", plugin_id="billing", requires=["customers"])
    registry = PluginManifestRegistry(plugins_dir)
    registry.discover()
    runtime = PluginRuntime(registry)
    runtime.load()

    result = runtime.list_results()[0]

    assert result.manifest is not None
    assert result.manifest.id == "billing"
    assert result.status == "failed"
    assert result.error_message == "missing dependency: customers"


def test_plugin_runtime_marks_invalid_manifest_as_failed_without_blocking_valid_plugin(
    tmp_path: Path,
) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir / "customers", plugin_id="customers")
    _write_plugin(plugins_dir / "broken", plugin_id="broken")
    (plugins_dir / "broken" / "plugin.json").write_text(
        json.dumps(
            {
                "id": "broken",
                "name": "Broken",
                "version": "0.1.0",
                "api_version": "1",
                "requires": [],
                "backend_entrypoint": "backend.plugin:register",
                "frontend_entrypoint": "frontend/register.ts",
                "permissions": ["wrong.sample.read"],
                "events": [],
                "description": "Broken plugin",
            }
        ),
        encoding="utf-8",
    )

    registry = PluginManifestRegistry(plugins_dir)
    registry.discover()
    runtime = PluginRuntime(registry)
    runtime.load()

    results = {result.plugin_id: result for result in runtime.list_results()}

    assert results["customers"].status == "enabled"
    assert results["broken"].status == "failed"
    assert results["broken"].error_message is not None


def test_plugin_runtime_accepts_register_returning_plugin_registration(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_root = plugins_dir / "billing"
    _write_plugin(plugin_root, plugin_id="billing")
    (plugin_root / "backend" / "plugin.py").write_text(
        "from systutor.sdk import PluginContext, PluginRegistration\n\n"
        "def register(context: PluginContext) -> PluginRegistration:\n"
        "    return PluginRegistration(\n"
        "        plugin_id='billing',\n"
        "        permissions=['billing.sample.read'],\n"
        "        events=['billing.sample.created'],\n"
        "    )\n",
        encoding="utf-8",
    )

    registry = PluginManifestRegistry(plugins_dir)
    registry.discover()
    runtime = PluginRuntime(registry)
    runtime.load()

    result = runtime.list_results()[0]

    assert result.status == "enabled"
    assert result.registration is not None
    assert result.registration == PluginRegistration(
        plugin_id="billing",
        permissions=["billing.sample.read"],
        events=["billing.sample.created"],
    )


def test_plugin_runtime_loads_fake_plugin(app) -> None:
    runtime = app.state.plugin_runtime
    alpha = next(
        result
        for result in runtime.list_results()
        if result.manifest is not None and result.manifest.id == "alpha"
    )

    assert alpha.status == "disabled"
    assert alpha.registration is None


def test_dispatcher_is_testable_without_redis(
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    bus = EventBus()
    bus.publish(
        db_session,
        event=EventContract(
            event_name="core.runtime.local_dispatch",
            module="core",
            tenant_id=seeded_demo["tenant_id"],
            branch_id=seeded_demo["branch_id"],
            actor_user_id=seeded_demo["user_id"],
            actor_type="user",
            entity_type="runtime",
            entity_id="evt-4",
            payload={"mode": "local"},
            metadata={},
        ),
    )

    result = dispatch_pending_outbox_events(db_session, bus, limit=10, max_retries=2)
    event_log = db_session.scalar(
        select(EventLog).where(EventLog.event_name == "core.runtime.local_dispatch")
    )
    outbox = db_session.scalar(
        select(EventOutbox).where(EventOutbox.event_name == "core.runtime.local_dispatch")
    )

    assert result["processed"] == 1
    assert event_log is not None
    assert outbox is not None
    assert outbox.status == "processed"
