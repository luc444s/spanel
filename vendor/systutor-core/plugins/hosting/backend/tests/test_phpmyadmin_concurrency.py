"""The SQL pool must be free while 40 upstream responses wait or stream."""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import httpx
from fastapi import FastAPI, HTTPException, Response
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from plugins.hosting.backend.models import HostingSite
from plugins.hosting.backend.phpmyadmin_session import (
    authorize_proxy_session,
    create_session,
    invalidate,
)
from plugins.hosting.backend.router import router
from plugins.hosting.backend.settings import HostingSettings
from systutor.core.database import Base
from systutor.core.errors import AppError
from systutor.kernel.auth.models import User
from systutor.kernel.permissions.models import Permission, Role, RolePermission, UserRole
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Tenant


def _database(path: Path):
    engine = create_engine(
        f"sqlite:///{path}",
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=0,
        pool_timeout=0.5,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Base.metadata.tables[name]
            for name in (
                "tenants",
                "branches",
                "users",
                "roles",
                "permissions",
                "user_roles",
                "role_permissions",
                "user_context_claims",
                "hosting_sites",
            )
        ],
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        tenant = Tenant(name="Demo", slug="demo")
        db.add(tenant)
        db.flush()
        user = User(
            tenant_id=tenant.id, email="demo@example.org", full_name="Demo", password_hash="unused"
        )
        site = HostingSite(tenant_id=tenant.id, container_name="spanel-demo-wp")
        role = Role(tenant_id=tenant.id, name="manager")
        permission = Permission(name="hosting.sites.manage")
        db.add_all([user, site, role, permission])
        db.flush()
        db.add_all(
            [
                UserRole(user_id=user.id, role_id=role.id),
                RolePermission(role_id=role.id, permission_id=permission.id),
            ]
        )
        identity = user.id, tenant.id, site.id
    return engine, factory, identity


def _checked_out(engine: Engine) -> int:
    return cast(QueuePool, engine.pool).checkedout()


def test_forty_concurrent_proxy_requests_release_sql_before_upstream(tmp_path, monkeypatch) -> None:
    engine, factory, (user_id, tenant_id, site_id) = _database(tmp_path / "pma.sqlite")
    record, _ = create_session(user_id, tenant_id, site_id, {"pma": "server-side"}, 120)
    arrived = threading.Event()
    release = threading.Event()
    guard = threading.Lock()
    count = 0

    class SlowUpstream(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            nonlocal count
            with guard:
                count += 1
                if count == 40:
                    arrived.set()
            release.wait(8)
            body = b"streamed download" * 5000
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowUpstream)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    import importlib

    router_module = importlib.import_module("plugins.hosting.backend.router")
    monkeypatch.setattr(
        router_module,
        "get_hosting_settings",
        lambda: HostingSettings(
            phpmyadmin_internal_url=f"http://127.0.0.1:{server.server_port}",
            phpmyadmin_timeout=10,
        ),
    )
    app = FastAPI()
    app.state.session_factory = factory
    app.include_router(router, prefix="/api/v1/plugins/hosting")

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", cookies={"spanel_pma": record.id}
        ) as client:
            url = f"/api/v1/plugins/hosting/sites/{site_id}/phpmyadmin/download.bin"
            responses = [asyncio.create_task(client.get(url)) for _ in range(40)]
            try:
                assert await asyncio.to_thread(arrived.wait, 6), f"only {count}/40 reached upstream"
                assert _checked_out(engine) == 0, "SQL connections held while upstream waits"
            finally:
                release.set()
            completed = await asyncio.gather(*responses)
            assert len(completed) == 40
            assert all(
                r.status_code == 200 and r.content.startswith(b"streamed") for r in completed
            )

    try:
        asyncio.run(exercise())
        assert _checked_out(engine) == 0
    finally:
        release.set()
        invalidate(record)
        server.shutdown()
        worker.join()
        server.server_close()
        engine.dispose()


def test_denied_authorization_also_returns_sql_connection(tmp_path) -> None:
    engine, factory, (user_id, tenant_id, site_id) = _database(tmp_path / "denied.sqlite")
    record, _ = create_session(user_id, tenant_id, site_id, {}, 120)
    app = FastAPI()
    app.state.session_factory = factory
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "app": app,
            "method": "GET",
            "path": "/proxy",
            "query_string": b"",
            "headers": [(b"cookie", f"spanel_pma={record.id}".encode())],
        }
    )
    try:
        with factory.begin() as db:
            user = db.get(User, user_id)
            assert user is not None
            user.is_active = False
        try:
            authorize_proxy_session(request, site_id)
        except HTTPException as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("inactive user accepted")
        assert _checked_out(engine) == 0
    finally:
        invalidate(record)
        engine.dispose()


def test_session_creation_releases_read_transaction_before_upstream(tmp_path) -> None:
    engine, factory, (user_id, tenant_id, site_id) = _database(tmp_path / "create.sqlite")
    import importlib

    route_module = importlib.import_module("plugins.hosting.backend.router")

    class SlowLogin:
        def phpmyadmin_login_credentials(self, site, context, client):
            assert site.id == site_id
            assert _checked_out(engine) == 0, "connection held during remote login"
            raise AppError(
                "upstream failed", status_code=502, code="hosting_phpmyadmin_login_failed"
            )

    try:
        with factory() as db:
            user = db.get(User, user_id)
            site = db.get(HostingSite, site_id)
            assert user is not None and site is not None
            context = TenantContext(tenant_id, None, user_id, ("hosting.sites.manage",), None)
            try:
                route_module.create_phpmyadmin_session(
                    response=Response(),
                    site=site,
                    tenant_context=context,
                    current_user=user,
                    service=SlowLogin(),
                    db=db,
                )
            except AppError as exc:
                assert exc.status_code == 502
            else:
                raise AssertionError("expected upstream failure")
            assert _checked_out(engine) == 0
    finally:
        engine.dispose()
