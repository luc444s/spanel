from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from systutor.api.seed import seed_demo_data
from systutor.contracts.plugins import PluginManifestContract
from systutor.core.config import Settings
from systutor.core.database import Base, build_engine
from systutor.kernel.auth.models import User
from systutor.kernel.auth.security import hash_password
from systutor.kernel.events.bus import EventBus
from systutor.kernel.events.models import EventLog, EventOutbox
from systutor.kernel.permissions.models import Permission
from systutor.kernel.tasks.dispatcher import TaskDispatcher, TaskDispatcherUnavailableError
from systutor.sdk import PluginContext


def login(client, email: str = "admin@example.com", password: str = "ChangeMe123!"):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def auth_headers(
    client,
    email: str = "admin@example.com",
    password: str = "ChangeMe123!",
) -> dict[str, str]:
    response = login(client, email=email, password=password)
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _write_runtime_plugin(
    plugin_root: Path,
    *,
    plugin_id: str,
    hooks_log: Path,
    listener_log: Path,
    task_log: Path,
    failing_hook: str | None = None,
) -> None:
    plugin_root.mkdir(parents=True, exist_ok=True)
    for directory in ["backend", "frontend", "migrations", "permissions", "events"]:
        (plugin_root / directory).mkdir(exist_ok=True)
    permission_name = f"{plugin_id}.sample.read"
    event_name = f"{plugin_id}.sample.created"

    manifest = {
        "id": plugin_id,
        "name": plugin_id.title(),
        "version": "0.1.0",
        "api_version": "1",
        "requires": [],
        "backend_entrypoint": "backend.plugin:register",
        "frontend_entrypoint": "frontend/register.ts",
        "permissions": [permission_name],
        "events": [event_name],
        "description": f"Plugin {plugin_id}",
    }
    (plugin_root / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin_root / "README.md").write_text(f"# {plugin_id}\n", encoding="utf-8")

    frontend_registration = dedent(
        f"""
        export function registerPlugin(ctx) {{
          return {{
            pluginId: "{plugin_id}",
            routes: [
              {{
                path: "{plugin_id}",
                title: "{plugin_id.title()}",
                component: () => null,
                requiredPermissions: ["{permission_name}"],
              }},
            ],
            navigation: [
              {{
                to: `${{ctx.appBasePath}}/{plugin_id}`,
                label: "{plugin_id.title()}",
                requiredPermissions: ["{permission_name}"],
              }},
            ],
            widgets: [],
          }};
        }}
        """
    ).strip()
    (plugin_root / "frontend" / "register.ts").write_text(
        f"{frontend_registration}\n",
        encoding="utf-8",
    )

    def build_hook_definition(hook_name: str, marker: str) -> str:
        maybe_raise = (
            f"    raise RuntimeError('hook {hook_name} exploded')\n"
            if failing_hook == hook_name
            else ""
        )
        return (
            f"def {hook_name}(context: PluginContext) -> None:\n"
            f'    _append(HOOKS_LOG, "{marker}")\n'
            f"{maybe_raise}\n"
        )

    hooks_code = "\n".join(
        [
            build_hook_definition("on_install", "install"),
            build_hook_definition("on_enable", "enable"),
            build_hook_definition("on_disable", "disable"),
            build_hook_definition("on_uninstall", "uninstall"),
        ]
    )
    backend_code = dedent(
        f"""
        from pathlib import Path

        from fastapi import APIRouter, Depends

        from systutor.kernel.auth.dependencies import require_permission
        from systutor.sdk import PluginContext

        HOOKS_LOG = Path({str(hooks_log)!r})
        LISTENER_LOG = Path({str(listener_log)!r})
        TASK_LOG = Path({str(task_log)!r})

        def _append(path: Path, value: str) -> None:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            path.write_text(f"{{existing}}{{value}}\\n", encoding="utf-8")

        def register(context: PluginContext) -> None:
            router = APIRouter()

            @router.get(
                "/runtime",
                dependencies=[Depends(require_permission("{permission_name}"))],
            )
            def runtime() -> dict[str, str]:
                event_log = context.publish_event(
                    "{event_name}",
                    {{"source": "runtime"}},
                    entity_type="plugin_runtime",
                    entity_id="runtime",
                    correlation_id="corr-{plugin_id}-runtime",
                )
                task_id = context.task_dispatcher.enqueue(
                    "{plugin_id}.fake_task",
                    {{"plugin_id": "{plugin_id}"}},
                )
                TASK_LOG.write_text(task_id, encoding="utf-8")
                return {{
                    "status": "ok",
                    "event_id": event_log.id,
                    "task_id": task_id,
                }}

            context.register_router(router)
            context.register_permissions(["{permission_name}"])
            context.register_events(["{event_name}"])

            def handle_created(event) -> None:
                LISTENER_LOG.write_text(event.event_name, encoding="utf-8")

            context.register_event_handler("{event_name}", handle_created)
        """
    ).strip()
    (plugin_root / "backend" / "plugin.py").write_text(
        f"{backend_code}\n\n{hooks_code}",
        encoding="utf-8",
    )


def _build_plugin_app(
    tmp_path: Path,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    *,
    plugin_id: str = "billing",
    failing_hook: str | None = None,
):
    plugins_dir = tmp_path / "plugins"
    hooks_log = tmp_path / f"{plugin_id}_hooks.log"
    listener_log = tmp_path / f"{plugin_id}_listener.log"
    task_log = tmp_path / f"{plugin_id}_task.log"
    _write_runtime_plugin(
        plugins_dir / plugin_id,
        plugin_id=plugin_id,
        hooks_log=hooks_log,
        listener_log=listener_log,
        task_log=task_log,
        failing_hook=failing_hook,
    )

    settings = test_settings.model_copy(
        update={
            "database_url": f"sqlite+pysqlite:///{tmp_path / f'{plugin_id}_runtime.db'}",
            "plugins_dir": plugins_dir,
        }
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    engine.dispose()

    class FakeTaskDispatcher:
        def enqueue(self, task_name: str, payload: dict) -> str:
            return f"{task_name}:{payload['plugin_id']}"

    monkeypatch.setattr(
        "systutor.core.lifecycle.build_task_dispatcher",
        lambda _settings: FakeTaskDispatcher(),
    )

    app = create_app(settings)
    with app.state.session_factory() as db:
        seeded = seed_demo_data(db, settings, app.state.plugin_runtime.list_results())
    return app, seeded, hooks_log, listener_log, task_log


def _create_limited_user(app, seeded_demo: dict[str, str]) -> tuple[str, str]:
    email = "limited-plugin@example.com"
    password = "Limited123!"
    with app.state.session_factory() as db:
        user = User(
            tenant_id=seeded_demo["tenant_id"],
            branch_id=seeded_demo["branch_id"],
            email=email,
            full_name="Limited Plugin User",
            password_hash=hash_password(password),
            is_active=True,
            is_superadmin=False,
        )
        db.add(user)
        db.commit()
    return email, password


def test_plugin_runtime_completion_e2e(
    tmp_path: Path,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, seeded_demo, hooks_log, listener_log, task_log = _build_plugin_app(
        tmp_path,
        test_settings,
        monkeypatch,
    )
    limited_email, limited_password = _create_limited_user(app, seeded_demo)

    with TestClient(app) as client:
        headers = auth_headers(client)
        limited_headers = auth_headers(client, email=limited_email, password=limited_password)

        assert client.get("/api/v1/plugins/billing/runtime", headers=headers).status_code == 404

        install_response = client.post("/api/v1/plugin-runtime/billing/install", headers=headers)
        assert install_response.status_code == 200
        assert install_response.json()["state"] == "installed"
        assert hooks_log.read_text(encoding="utf-8").splitlines() == ["install"]

        with app.state.session_factory() as db:
            permission = db.scalar(
                select(Permission).where(Permission.name == "billing.sample.read")
            )
            assert permission is not None
            installed_event = db.scalar(
                select(EventLog).where(
                    EventLog.event_name == "core.plugin.installed",
                    EventLog.entity_id == "billing",
                )
            )
            assert installed_event is not None

        enable_response = client.post("/api/v1/plugin-runtime/billing/enable", headers=headers)
        assert enable_response.status_code == 200
        assert enable_response.json()["state"] == "enabled"
        assert hooks_log.read_text(encoding="utf-8").splitlines() == ["install", "enable"]

        assert client.get("/api/v1/system/health").status_code == 200
        assert client.get("/api/v1/system/ready").status_code == 200
        detail_response = client.get("/api/v1/system/plugin-runtime/billing", headers=headers)
        assert detail_response.status_code == 200

        assert client.get("/api/v1/plugins/billing/runtime").status_code == 401
        forbidden_response = client.get(
            "/api/v1/plugins/billing/runtime",
            headers=limited_headers,
        )
        assert forbidden_response.status_code == 403

        route_response = client.get("/api/v1/plugins/billing/runtime", headers=headers)
        assert route_response.status_code == 200
        assert route_response.json()["status"] == "ok"
        assert task_log.read_text(encoding="utf-8") == "billing.fake_task:billing"

        with app.state.session_factory() as db:
            domain_event = db.scalar(
                select(EventLog).where(
                    EventLog.event_name == "billing.sample.created",
                    EventLog.entity_id == "runtime",
                )
            )
            assert domain_event is not None
            assert (
                db.scalar(select(EventOutbox).where(EventOutbox.event_log_id == domain_event.id))
                is not None
            )
            dispatch_result = app.state.event_bus.dispatch_pending(db, limit=25, max_retries=2)
            db.commit()
            assert dispatch_result["processed"] >= 1

        assert listener_log.read_text(encoding="utf-8") == "billing.sample.created"

        disable_response = client.post("/api/v1/plugin-runtime/billing/disable", headers=headers)
        assert disable_response.status_code == 200
        assert disable_response.json()["state"] == "disabled"
        assert hooks_log.read_text(encoding="utf-8").splitlines() == [
            "install",
            "enable",
            "disable",
        ]
        assert client.get("/api/v1/plugins/billing/runtime", headers=headers).status_code == 404

        uninstall_response = client.post(
            "/api/v1/plugin-runtime/billing/uninstall",
            headers=headers,
        )
        assert uninstall_response.status_code == 200
        assert uninstall_response.json()["state"] == "uninstalled"
        assert hooks_log.read_text(encoding="utf-8").splitlines() == [
            "install",
            "enable",
            "disable",
            "uninstall",
        ]
        assert client.get("/api/v1/plugins/billing/runtime", headers=headers).status_code == 404

        with app.state.session_factory() as db:
            events = list(
                db.scalars(
                    select(EventLog.event_name).where(
                        EventLog.entity_id == "billing",
                        EventLog.event_name.in_(
                            [
                                "core.plugin.installed",
                                "core.plugin.enabled",
                                "core.plugin.disabled",
                                "core.plugin.uninstalled",
                            ]
                        ),
                    )
                )
            )
            assert sorted(events) == [
                "core.plugin.disabled",
                "core.plugin.enabled",
                "core.plugin.installed",
                "core.plugin.uninstalled",
            ]


def test_failed_plugin_does_not_expose_routes_and_persists_error(
    tmp_path: Path,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _seeded_demo, _hooks_log, _listener_log, _task_log = _build_plugin_app(
        tmp_path,
        test_settings,
        monkeypatch,
        plugin_id="broken",
        failing_hook="on_enable",
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        headers = auth_headers(client)

        install_response = client.post("/api/v1/plugin-runtime/broken/install", headers=headers)
        assert install_response.status_code == 200

        enable_response = client.post("/api/v1/plugin-runtime/broken/enable", headers=headers)
        assert enable_response.status_code == 500
        assert client.get("/api/v1/plugins/broken/runtime", headers=headers).status_code == 404

        detail_response = client.get("/api/v1/system/plugin-runtime/broken", headers=headers)
        assert detail_response.status_code == 200
        assert detail_response.json()["state"] == "failed"
        assert "hook on_enable exploded" in detail_response.json()["last_error"]

        with app.state.session_factory() as db:
            failed_event = db.scalar(
                select(EventLog).where(
                    EventLog.event_name == "core.plugin.failed",
                    EventLog.entity_id == "broken",
                )
            )
            assert failed_event is not None


def test_plugin_context_publish_event_validates_namespace_and_preserves_scope(
    app,
    seeded_demo: dict[str, str],
) -> None:
    seen: list[str] = []
    bus = EventBus()

    def listener(event) -> None:
        seen.append(event.event_name)

    bus.register_listener("billing.sample.created", listener, source="tests")
    manifest = PluginManifestContract(
        id="billing",
        name="Billing",
        version="0.1.0",
        api_version="1",
        requires=[],
        backend_entrypoint="backend.plugin:register",
        frontend_entrypoint="frontend/register.ts",
        permissions=["billing.sample.read"],
        events=["billing.sample.created"],
        description="Billing test plugin",
    )
    context = PluginContext(
        manifest,
        event_bus=bus,
        db_session_provider=app.state.session_factory,
    )

    event_log = context.publish_event(
        "billing.sample.created",
        {"source": "pytest"},
        tenant_id=seeded_demo["tenant_id"],
        branch_id=seeded_demo["branch_id"],
        actor_user_id=seeded_demo["user_id"],
        entity_type="invoice",
        entity_id="inv-1",
        correlation_id="corr-billing-1",
    )

    with app.state.session_factory() as db:
        persisted = db.scalar(select(EventLog).where(EventLog.id == event_log.id))
        outbox = db.scalar(select(EventOutbox).where(EventOutbox.event_log_id == event_log.id))
        assert persisted is not None
        assert persisted.tenant_id == seeded_demo["tenant_id"]
        assert persisted.branch_id == seeded_demo["branch_id"]
        assert persisted.correlation_id == "corr-billing-1"
        assert outbox is not None

        result = bus.dispatch_pending(db, limit=10, max_retries=2)
        db.commit()
        assert result["processed"] >= 1

    assert seen == ["billing.sample.created"]

    with pytest.raises(ValueError, match="plugin events must use the plugin id namespace"):
        context.publish_event("core.user.created", {})


def test_task_dispatcher_is_mockable_and_fails_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMessage:
        def __init__(
            self,
            *,
            queue_name: str,
            actor_name: str,
            args: tuple,
            kwargs: dict,
            options: dict,
            message_id: str,
            message_timestamp,
        ) -> None:
            self.queue_name = queue_name
            self.actor_name = actor_name
            self.args = args
            self.kwargs = kwargs
            self.options = options
            self.message_id = message_id
            self.message_timestamp = message_timestamp

    class FakeDramatiq:
        Message = FakeMessage

    sent_messages: list[FakeMessage] = []

    class FakeBroker:
        def enqueue(self, message: FakeMessage) -> None:
            sent_messages.append(message)

    monkeypatch.setattr(
        "systutor.kernel.tasks.dispatcher.importlib.import_module",
        lambda _name: FakeDramatiq,
    )

    dispatcher = TaskDispatcher(broker=FakeBroker())
    task_id = dispatcher.enqueue("billing.fake_task", {"plugin_id": "billing"})

    assert task_id
    assert len(sent_messages) == 1
    assert sent_messages[0].actor_name == "billing.fake_task"

    unavailable = TaskDispatcher(broker=None, reason="broker missing")
    with pytest.raises(TaskDispatcherUnavailableError, match="broker missing"):
        unavailable.enqueue("billing.fake_task", {"plugin_id": "billing"})
