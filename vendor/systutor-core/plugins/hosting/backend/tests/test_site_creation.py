"""Creation rules, kind guards and static cleanup (A.SPEC 0031)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.hosting.backend import service as service_module
from plugins.hosting.backend.models import HostingSite
from plugins.hosting.backend.schemas import CreateSiteRequest
from plugins.hosting.backend.service import HostingService
from plugins.hosting.backend.settings import HostingSettings
from systutor.contracts.site_domain_publisher import (
    get_site_domain_publisher,
    register_site_domain_publisher,
)
from systutor.contracts.site_namespace import register_site_suffix_provider
from systutor.core.database import Base
from systutor.core.errors import AppError
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Tenant


class FakeWordPressProvisioner:
    calls: list[tuple[str, str, str, str]] = []
    cleaned: list[str] = []

    def __init__(self, settings) -> None:
        pass

    def create(self, names, tenant_id, domain, admin_email):  # noqa: ANN001
        type(self).calls.append((names.wp, tenant_id, domain, admin_email))
        return "generated-password"

    def cleanup(self, names, tenant_id, resources=None):  # noqa: ANN001
        type(self).cleaned.append(names.wp)
        return []

    def command(self, *args, input=None):  # noqa: ANN001
        return ""


class FakeStaticProvisioner:
    calls: list[tuple[str, str, str, bool]] = []
    cleaned: list[str] = []

    def __init__(self, settings) -> None:
        pass

    def create(self, names, tenant_id, domain, *, traefik=True):  # noqa: ANN001
        type(self).calls.append((names.container, tenant_id, domain, traefik))

    def cleanup(self, names, tenant_id, resources=None):  # noqa: ANN001
        type(self).cleaned.append(names.container)
        return []


class FakePublisher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.published: list[tuple[str, str]] = []
        self.unpublished: list[str] = []

    def publish(self, container: str, domain: str) -> None:
        if self.fail:
            raise RuntimeError("proxy down")
        self.published.append((container, domain))

    def unpublish(self, domain: str) -> None:
        self.unpublished.append(domain)


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'hosting.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="t1", name="Tenant", slug="tenant"))
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def reset_fakes(monkeypatch):
    FakeWordPressProvisioner.calls = []
    FakeWordPressProvisioner.cleaned = []
    FakeStaticProvisioner.calls = []
    FakeStaticProvisioner.cleaned = []
    monkeypatch.setattr(service_module, "Provisioner", FakeWordPressProvisioner)
    monkeypatch.setattr(service_module, "StaticProvisioner", FakeStaticProvisioner)


def context(user_id: str = "u1") -> TenantContext:
    return TenantContext("t1", None, user_id, (), None)


def service(db: Session) -> HostingService:
    return HostingService(db, settings=HostingSettings(docker_transport="local"))


def suffix_app(suffix: str | None = "spanel") -> FastAPI:
    app = FastAPI()
    if suffix is not None:
        register_site_suffix_provider(app, "test", lambda db, tenant_id: suffix)
    return app


def test_create_request_forbids_admin_email() -> None:
    with pytest.raises(Exception):
        CreateSiteRequest.model_validate(
            {"name": "sitio", "domain": "sitio.spanel", "admin_email": "a@b.com"}
        )


def test_static_is_valid_and_kind_defaults_to_wordpress() -> None:
    static = CreateSiteRequest.model_validate(
        {"kind": "static", "name": "sitio", "domain": "sitio.spanel"}
    )
    assert static.kind == "static"
    default = CreateSiteRequest.model_validate({"name": "sitio", "domain": "sitio.spanel"})
    assert default.kind == "wordpress"


def test_invalid_kind_is_rejected() -> None:
    with pytest.raises(Exception):
        CreateSiteRequest.model_validate({"kind": "otro", "name": "sitio", "domain": "sitio.spanel"})


def test_missing_suffix_returns_409_without_provisioning(db: Session) -> None:
    with pytest.raises(AppError) as exc:
        service(db).create_site(
            context(), "sitio", "sitio.spanel", kind="static", app=suffix_app(None)
        )
    assert exc.value.code == "hosting_site_suffix_missing"
    assert FakeStaticProvisioner.calls == []
    assert FakeWordPressProvisioner.calls == []


def test_domain_outside_suffix_returns_409_without_provisioning(db: Session) -> None:
    with pytest.raises(AppError) as exc:
        service(db).create_site(
            context(), "sitio", "otro.com", kind="static", app=suffix_app("spanel")
        )
    assert exc.value.code == "hosting_site_domain_outside_suffix"
    assert FakeStaticProvisioner.calls == []


def test_domain_already_in_hosting_sites_conflicts(db: Session) -> None:
    db.add(
        HostingSite(
            tenant_id="t1",
            container_name="spanel-otro-web",
            domain="sitio.spanel",
            kind="static",
        )
    )
    db.commit()
    with pytest.raises(AppError) as exc:
        service(db).create_site(
            context(), "sitio", "sitio.spanel", kind="static", app=suffix_app()
        )
    assert exc.value.code == "hosting_site_conflict"
    assert FakeStaticProvisioner.calls == []


def test_wordpress_uses_authenticated_user_email(db: Session) -> None:
    db.add(
        User(
            id="u1",
            tenant_id="t1",
            email="lucas@example.com",
            full_name="Lucas",
            password_hash="x",
        )
    )
    db.commit()
    response = service(db).create_site(
        context(), "demowp", "demowp.spanel", app=suffix_app()
    )
    assert FakeWordPressProvisioner.calls[0][3] == "lucas@example.com"
    assert response.admin_password == "generated-password"
    assert response.site.kind == "wordpress"


def test_wordpress_falls_back_to_admin_at_domain(db: Session) -> None:
    service(db).create_site(context("missing"), "demowp", "demowp.spanel", app=suffix_app())
    assert FakeWordPressProvisioner.calls[0][3] == "admin@demowp.spanel"


def test_static_with_publisher_uses_no_traefik_labels(db: Session) -> None:
    app = suffix_app()
    publisher = FakePublisher()
    register_site_domain_publisher(app, "domains", publisher)
    response = service(db).create_site(
        context(), "pagina", "pagina.spanel", kind="static", app=app
    )
    assert FakeStaticProvisioner.calls[0][3] is False
    assert publisher.published == [("spanel-pagina-web", "pagina.spanel")]
    assert response.admin_password is None
    assert response.site.kind == "static"
    assert FakeWordPressProvisioner.calls == []


def test_static_without_publisher_keeps_traefik_labels(db: Session) -> None:
    service(db).create_site(
        context(), "pagina", "pagina.spanel", kind="static", app=suffix_app()
    )
    assert FakeStaticProvisioner.calls[0][3] is True
    assert get_site_domain_publisher(None) is None


def test_publish_failure_cleans_up_and_inserts_no_row(db: Session) -> None:
    app = suffix_app()
    register_site_domain_publisher(app, "domains", FakePublisher(fail=True))
    with pytest.raises(AppError) as exc:
        service(db).create_site(
            context(), "pagina", "pagina.spanel", kind="static", app=app
        )
    assert exc.value.code == "hosting_provision_failed"
    assert FakeStaticProvisioner.cleaned == ["spanel-pagina-web"]
    assert db.query(HostingSite).count() == 0


@pytest.mark.parametrize(
    "method,args",
    [
        ("get_site_config", ()),
        ("update_site_config", (context(), {})),
        ("site_database", (context(),)),
        ("list_users", ()),
        ("rotate_site_salts", (context(),)),
        ("reset_password", ()),
    ],
)
def test_wordpress_only_methods_reject_static(db: Session, method: str, args: tuple) -> None:
    site = HostingSite(
        id="s1",
        tenant_id="t1",
        container_name="spanel-pagina-web",
        domain="pagina.spanel",
        kind="static",
    )
    kwargs = {"wp_user_id": 1, "password": None, "tenant_context": context()}
    with pytest.raises(AppError) as exc:
        getattr(service(db), method)(site, *args, **kwargs) if method == "reset_password" else getattr(
            service(db), method
        )(site, *args)
    assert exc.value.code == "hosting_site_wrong_kind"


class DeletingProvisioner:
    def __init__(self, settings) -> None:
        self.deleted: list[tuple[str, str]] = []

    def exists(self, kind: str, name: str) -> bool:
        return True

    def _owned(self, kind: str, name: str, names, tenant_id: str) -> bool:  # noqa: ANN001
        return True

    def cleanup(self, names, tenant_id, resources=None):  # noqa: ANN001
        self.deleted = list(resources or [])
        DeletingProvisioner.last = self
        return []


def test_delete_static_unpublishes_and_removes_owned_resources(
    db: Session, monkeypatch
) -> None:
    DeletingProvisioner.last = None
    monkeypatch.setattr(service_module, "Provisioner", DeletingProvisioner)
    app = suffix_app()
    publisher = FakePublisher()
    register_site_domain_publisher(app, "domains", publisher)
    site = HostingSite(
        id="s1",
        tenant_id="t1",
        container_name="spanel-pagina-web",
        domain="pagina.spanel",
        kind="static",
    )
    db.add(site)
    db.commit()
    service(db).delete_site(site, context(), app=app)
    db.commit()
    assert publisher.unpublished == ["pagina.spanel"]
    assert DeletingProvisioner.last is not None
    assert ("container", "spanel-pagina-web") in DeletingProvisioner.last.deleted
    assert db.get(HostingSite, "s1") is None


def test_delete_static_refuses_foreign_resources(db: Session, monkeypatch) -> None:
    class Foreign(DeletingProvisioner):
        def _owned(self, kind, name, names, tenant_id):  # noqa: ANN001
            return False

    Foreign.last = None
    monkeypatch.setattr(service_module, "Provisioner", Foreign)
    app = suffix_app()
    publisher = FakePublisher()
    register_site_domain_publisher(app, "domains", publisher)
    site = HostingSite(
        id="s1",
        tenant_id="t1",
        container_name="spanel-pagina-web",
        domain="pagina.spanel",
        kind="static",
    )
    db.add(site)
    db.commit()
    with pytest.raises(AppError) as exc:
        service(db).delete_site(site, context(), app=app)
    assert exc.value.code == "hosting_site_not_managed"
    assert publisher.unpublished == []
    assert db.get(HostingSite, "s1") is not None


def test_static_is_listed_with_kind_and_start_stop_work(db: Session, monkeypatch) -> None:
    db.add(
        HostingSite(
            id="s1",
            tenant_id="t1",
            container_name="spanel-pagina-web",
            domain="pagina.spanel",
            kind="static",
        )
    )
    db.commit()
    info = {
        "Config": {
            "Image": "nginx:alpine",
            "Labels": {"spanel.managed": "true", "spanel.site": "pagina"},
        },
        "State": {"Status": "running"},
    }
    svc = service(db)
    monkeypatch.setattr(svc, "_inspect", lambda container: info)
    listed = svc.list_sites("t1")
    assert listed.sites[0].kind == "static"
    started = svc.start_site(db.get(HostingSite, "s1"), context())
    assert started.kind == "static"
    stopped = svc.stop_site(db.get(HostingSite, "s1"), context())
    assert stopped.status == "exited"
