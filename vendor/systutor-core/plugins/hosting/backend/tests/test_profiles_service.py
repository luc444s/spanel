"""Profile CRUD at service level: codes, guards and audit without env values."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.hosting.backend.models import HostingSite, StaticProfile
from plugins.hosting.backend.schemas import StaticProfileCreate, StaticProfileUpdate
from plugins.hosting.backend.service import HostingService
from plugins.hosting.backend.settings import HostingSettings
from systutor.core.database import Base
from systutor.core.errors import AppError, NotFoundError
from systutor.kernel.audit.models import AuditLog
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Tenant


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'profiles.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="t1", name="Tenant", slug="tenant"))
        # Migration 003 equivalent: the builtin from every deployment.
        session.add(
            StaticProfile(
                id="nginx",
                name="Nginx (static)",
                image="nginx:alpine",
                web_root="/usr/share/nginx/html",
                container_port=80,
                health_mode="marker",
            )
        )
        session.commit()
        yield session
    engine.dispose()


def context(user_id: str = "u1") -> TenantContext:
    return TenantContext("t1", None, user_id, (), None)


def service(db: Session) -> HostingService:
    return HostingService(db, settings=HostingSettings(docker_transport="local"))


def profile_data(**overrides) -> dict:
    data = dict(
        id="boot",
        name="Bootstrap (Apache)",
        image="httpd:alpine",
        web_root="/usr/local/apache2/htdocs",
        container_port=80,
        health_mode="http_200",
    )
    data.update(overrides)
    return data


# --- V2 (schemas) ------------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"image": "--privileged"},
        {"image": ""},
        {"web_root": "srv/relative"},
        {"container_port": 0},
        {"container_port": 65536},
        {"env_json": {"A": 1}},
    ],
)
def test_invalid_profile_shapes_are_rejected_by_the_schema(override) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        StaticProfileCreate.model_validate(profile_data(**override))


def test_valid_profile_passes_the_schema() -> None:
    body = StaticProfileCreate.model_validate(
        profile_data(env_json={"FOO": "1"})
    )
    assert body.env_json == {"FOO": "1"}


# --- V3 (CRUD) ----------------------------------------------------------------


def test_get_lists_the_builtin(db: Session) -> None:
    summary = service(db).get_profile("nginx")
    assert summary.image == "nginx:alpine"
    assert summary.builtin is True


def test_post_creates_and_must_be_unique(db: Session) -> None:
    summary = service(db).create_profile(context(), StaticProfileCreate.model_validate(profile_data(
        env_json={"HUGO_BASE_URL": "http://x"}
    )))
    assert summary.id == "boot"
    # The response never carries env VALUES, only names.
    assert summary.env_keys == ["HUGO_BASE_URL"]
    with pytest.raises(AppError) as exc:
        service(db).create_profile(context(), StaticProfileCreate.model_validate(profile_data()))
    assert exc.value.code == "hosting_profile_conflict"
    assert exc.value.status_code == 409


def test_put_updates_the_builtin_image_without_migrations(db: Session) -> None:
    summary = service(db).update_profile(
        context(),
        "nginx",
        StaticProfileUpdate.model_validate({"image": "nginx:1.27"}),
    )
    assert summary.image == "nginx:1.27"


def test_put_on_nginx_cannot_rename(db: Session) -> None:
    with pytest.raises(AppError) as exc:
        service(db).update_profile(
            context(), "nginx", StaticProfileUpdate.model_validate({"name": "Otro"})
        )
    assert exc.value.code == "hosting_profile_builtin"
    assert exc.value.status_code == 409


def test_delete_nginx_is_builtin_guarded(db: Session) -> None:
    with pytest.raises(AppError) as exc:
        service(db).delete_profile(context(), "nginx")
    assert exc.value.code == "hosting_profile_builtin"
    assert db.get(StaticProfile, "nginx") is not None


def test_delete_in_use_is_guarded_and_row_survives(db: Session) -> None:
    service(db).create_profile(context(), StaticProfileCreate.model_validate(profile_data()))
    db.add(
        HostingSite(
            id="s1",
            tenant_id="t1",
            container_name="spanel-sitio-web",
            domain="sitio.spanel",
            kind="static",
            profile_id="boot",
        )
    )
    db.commit()
    with pytest.raises(AppError) as exc:
        service(db).delete_profile(context(), "boot")
    assert exc.value.code == "hosting_profile_in_use"
    assert exc.value.status_code == 409
    assert db.get(StaticProfile, "boot") is not None


def test_delete_a_free_profile_works(db: Session) -> None:
    service(db).create_profile(context(), StaticProfileCreate.model_validate(profile_data()))
    service(db).delete_profile(context(), "boot")
    with pytest.raises(NotFoundError):
        service(db).get_profile("boot")


def test_unknown_profile_id_is_404(db: Session) -> None:
    with pytest.raises(NotFoundError):
        service(db).get_profile("ghost")


def test_every_change_audits_with_the_id_and_without_env_values(db: Session) -> None:
    service(db).create_profile(
        context(), StaticProfileCreate.model_validate(profile_data(env_json={"SECRET": "s3cr3t"}))
    )
    service(db).update_profile(
        context(), "boot", StaticProfileUpdate.model_validate({"image": "httpd:2.4"})
    )
    service(db).update_profile(
        context(), "nginx", StaticProfileUpdate.model_validate({"image": "nginx:1.27"})
    )
    with pytest.raises(AppError):
        service(db).delete_profile(context(), "nginx")
    service(db).delete_profile(context(), "boot")
    db.commit()
    rows = db.scalars(
        select(AuditLog).where(AuditLog.module == "hosting").order_by(AuditLog.id)
    ).all()
    actions = {row.action for row in rows}
    assert actions == {
        "hosting.profile.created",
        "hosting.profile.updated",
        "hosting.profile.deleted",
    }
    dumped = str([row.details for row in rows]) + str(rows)
    assert "s3cr3t" not in dumped
    assert "SECRET" not in dumped
    assert all(row.entity_id for row in rows)
