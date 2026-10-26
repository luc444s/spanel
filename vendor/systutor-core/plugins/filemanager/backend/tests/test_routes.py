from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import cast

import httpx
from fastapi import FastAPI
from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from plugins.filemanager.backend import router as routes
from plugins.filemanager.backend.tickets import consume
from plugins.hosting.backend.models import HostingSite
from plugins.hosting.backend.register import resolve_hosting_files
from systutor.contracts.file_targets import register_file_target
from systutor.core.config import Settings
from systutor.core.database import Base
from systutor.kernel.auth.models import User
from systutor.kernel.auth.security import create_access_token
from systutor.kernel.permissions.models import Permission, Role, RolePermission, UserRole
from systutor.kernel.tenants.models import Tenant


def _app(path: Path) -> tuple[FastAPI, Engine, str, str, str]:
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
                "audit_logs",
            )
        ],
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        tenant = Tenant(name="Demo", slug="demo")
        other = Tenant(name="Other", slug="other")
        db.add_all([tenant, other])
        db.flush()
        user = User(
            tenant_id=tenant.id, email="demo@example.org", full_name="Demo", password_hash="unused"
        )
        own_site = HostingSite(tenant_id=tenant.id, container_name="demo-wp")
        other_site = HostingSite(tenant_id=other.id, container_name="other-wp")
        role = Role(tenant_id=tenant.id, name="files")
        db.add_all([user, own_site, other_site, role])
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        for name in (
            "filemanager.files.read",
            "filemanager.files.manage",
            "hosting.sites.read",
            "hosting.sites.manage",
        ):
            permission = Permission(name=name)
            db.add(permission)
            db.flush()
            db.add(RolePermission(role_id=role.id, permission_id=permission.id))
        ids = user.id, tenant.id, own_site.id, other_site.id
    app = FastAPI()
    app.state.settings = Settings(jwt_secret_key="test-secret")
    app.state.session_factory = factory
    register_file_target(app, "hosting", resolve_hosting_files)
    app.include_router(routes.router, prefix="/api/v1/plugins/filemanager")
    user_id, tenant_id, own_id, other_id = ids
    token = create_access_token(
        settings=app.state.settings,
        subject=user_id,
        tenant_id=tenant_id,
        email="demo@example.org",
        branch_id=None,
    )
    return app, engine, token, own_id, other_id


def test_provider_scopes_site_and_upload_requires_manage(tmp_path, monkeypatch) -> None:
    app, engine, token, own_id, other_id = _app(tmp_path / "owned.sqlite")
    seen: list[tuple[str, str]] = []

    def fake_operation(target, op, env, **kwargs):
        seen.append((target.container, op))
        return {"ok": True, "entries": []}

    monkeypatch.setattr(routes, "operation", fake_operation)
    monkeypatch.setattr(
        routes,
        "upload",
        lambda target, op, env, source, limit, **kwargs: (
            seen.append((target.container, source.read().decode())) or {"ok": True}
        ),
    )

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            base = "/api/v1/plugins/filemanager/targets/hosting/"
            assert (await client.get(base + other_id + "/entries")).status_code == 404
            assert (await client.get(base + own_id + "/entries")).status_code == 200
            assert (
                await client.post(base + other_id + "/uploads", files={"file": ("x.txt", b"abc")})
            ).status_code == 404
            assert (
                await client.post(base + own_id + "/uploads", files={"file": ("x.txt", b"abc")})
            ).status_code == 200
            assert (
                await client.post(base + own_id + "/uploads", files={"file": ("../x", b"abc")})
            ).status_code == 400
            assert (
                await client.get(
                    base + own_id + "/entries", headers={"Authorization": "Bearer invalid"}
                )
            ).status_code == 401
            link = await client.post(base + own_id + "/download-tickets?path=wp-config.php")
            assert link.status_code == 200
            ticket = link.json()["url"].rsplit("/", 1)[-1]
            record = consume(ticket)
            assert record is not None
            assert record["resource_id"] == own_id and record["provider"] == "hosting"
            assert record["path"] == "wp-config.php"
            assert consume(ticket) is None

    try:
        asyncio.run(exercise())
        assert seen == [("demo-wp", "list"), ("demo-wp", "abc")]
    finally:
        engine.dispose()


def test_thirty_two_concurrent_requests_do_not_hold_sql_during_container_io(
    tmp_path, monkeypatch
) -> None:
    app, engine, token, own_id, _ = _app(tmp_path / "parallel.sqlite")
    arrived = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    count = 0

    def blocked_operation(target, op, env, **kwargs):
        nonlocal count
        with lock:
            count += 1
            if count == 32:
                arrived.set()
        release.wait(8)
        return {"ok": True, "entries": []}

    monkeypatch.setattr(routes, "operation", blocked_operation)

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            url = f"/api/v1/plugins/filemanager/targets/hosting/{own_id}/entries"
            requests = [asyncio.create_task(client.get(url)) for _ in range(32)]
            try:
                assert await asyncio.to_thread(arrived.wait, 6), f"{count}/32 reached container I/O"
                assert cast(QueuePool, engine.pool).checkedout() == 0
            finally:
                release.set()
            assert all(r.status_code == 200 for r in await asyncio.gather(*requests))

    try:
        asyncio.run(exercise())
        assert cast(QueuePool, engine.pool).checkedout() == 0
    finally:
        release.set()
        engine.dispose()


def test_revoked_permission_and_provider_can_supply_a_different_root(tmp_path, monkeypatch) -> None:
    app, engine, token, own_id, _ = _app(tmp_path / "revoked.sqlite")
    from systutor.contracts.file_targets import FileTarget, register_file_target

    register_file_target(
        app,
        "other",
        lambda db, tenant, resource, write, permissions: (
            FileTarget("different-container", "/srv/custom", resource)
            if resource == "mine"
            else None
        ),
    )
    seen = []

    def fake_operation(target, op, env, **kwargs):
        seen.append((target.container, target.root))
        return {"ok": True, "entries": []}

    monkeypatch.setattr(routes, "operation", fake_operation)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            base = "/api/v1/plugins/filemanager/targets/"
            assert (await client.get(base + "other/mine/entries")).status_code == 200
            assert (await client.get(base + "other/unknown/entries")).status_code == 404
            with app.state.session_factory.begin() as db:
                permission_id = db.scalar(
                    select(Permission.id).where(Permission.name == "filemanager.files.read")
                )
                db.execute(
                    delete(RolePermission).where(RolePermission.permission_id == permission_id)
                )
            assert (await client.get(base + "hosting/" + own_id + "/entries")).status_code == 403
            assert (
                await client.post(base + "hosting/" + own_id + "/download-tickets?path=x")
            ).status_code == 403

    try:
        asyncio.run(exercise())
        assert seen == [("different-container", "/srv/custom")]
    finally:
        engine.dispose()


def test_static_sites_use_the_volume_runtime(tmp_path) -> None:
    app, engine, _token, own_id, _ = _app(tmp_path / "static.sqlite")
    with app.state.session_factory.begin() as db:
        wordpress = db.get(HostingSite, own_id)
        assert wordpress is not None
        static = HostingSite(
            tenant_id=wordpress.tenant_id,
            container_name="spanel-demo-web",
            domain="demo.spanel",
            kind="static",
        )
        db.add(static)
        db.flush()
        static_id = static.id
        tenant_id = wordpress.tenant_id

    with app.state.session_factory() as db:
        target = resolve_hosting_files(
            db, tenant_id, static_id, False, ("hosting.sites.read",)
        )
        assert target is not None
        assert target.runtime == "volume"
        assert target.root == "/usr/share/nginx/html"
    engine.dispose()
