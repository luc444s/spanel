from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from systutor.kernel.plugins.migrations import list_plugin_migrations
from systutor.kernel.plugins.runtime import DiscoveredPlugin


def _legacy_tables(metadata: MetaData) -> dict[str, Table]:
    users = Table(
        "users",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("tenant_id", String(36), nullable=False),
        Column("email", String(320), nullable=False),
    )
    roles = Table(
        "roles",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("tenant_id", String(36), nullable=False),
        Column("name", String(100), nullable=False),
        Column("description", Text),
        Column("is_active", Boolean, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    permissions = Table(
        "permissions",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("name", String(150), nullable=False),
    )
    role_permissions = Table(
        "role_permissions",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("role_id", ForeignKey("roles.id"), nullable=False),
        Column("permission_id", ForeignKey("permissions.id"), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    user_roles = Table(
        "user_roles",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("user_id", ForeignKey("users.id"), nullable=False),
        Column("role_id", ForeignKey("roles.id"), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    return {
        "users": users,
        "roles": roles,
        "permissions": permissions,
        "role_permissions": role_permissions,
        "user_roles": user_roles,
    }


def test_runtime_migration_preserves_permissions_and_assigns_roleless_users(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'single-role.sqlite'}")
    metadata = MetaData()
    tables = _legacy_tables(metadata)
    metadata.create_all(engine)
    now = datetime.now(UTC)

    with engine.begin() as connection:
        connection.execute(
            tables["users"].insert(),
            [
                {"id": "u-many", "tenant_id": "t1", "email": "many@example.com"},
                {"id": "u-empty", "tenant_id": "t1", "email": "empty@example.com"},
            ],
        )
        connection.execute(
            tables["roles"].insert(),
            [
                {
                    "id": "r1", "tenant_id": "t1", "name": "r1",
                    "is_active": True, "created_at": now, "updated_at": now,
                },
                {
                    "id": "r2", "tenant_id": "t1", "name": "r2",
                    "is_active": True, "created_at": now, "updated_at": now,
                },
            ],
        )
        connection.execute(
            tables["permissions"].insert(),
            [{"id": "p1", "name": "p1"}, {"id": "p2", "name": "p2"}],
        )
        connection.execute(
            tables["role_permissions"].insert(),
            [
                {"id": "rp1", "role_id": "r1", "permission_id": "p1", "created_at": now},
                {"id": "rp2", "role_id": "r2", "permission_id": "p2", "created_at": now},
            ],
        )
        connection.execute(
            tables["user_roles"].insert(),
            [
                {"id": "ur1", "user_id": "u-many", "role_id": "r1", "created_at": now},
                {"id": "ur2", "user_id": "u-many", "role_id": "r2", "created_at": now},
            ],
        )

    root = Path(__file__).resolve().parents[1] / "plugins" / "tenant"
    discovered = DiscoveredPlugin(plugin_id="tenant", root=root)
    migration = next(
        step for step in list_plugin_migrations(discovered)
        if step.revision == "003_single_user_role"
    )
    with Session(engine) as db:
        migration.upgrade(db)
        migration.upgrade(db)
        db.commit()

    with Session(engine) as db:
        assignments = db.execute(
            select(
                tables["user_roles"].c.user_id,
                tables["user_roles"].c.role_id,
            ).order_by(tables["user_roles"].c.user_id)
        ).all()
        assert [row.user_id for row in assignments] == ["u-empty", "u-many"]
        combined_role = next(row.role_id for row in assignments if row.user_id == "u-many")
        permission_ids = list(
            db.scalars(
                select(tables["role_permissions"].c.permission_id)
                .where(tables["role_permissions"].c.role_id == combined_role)
                .order_by(tables["role_permissions"].c.permission_id)
            )
        )
        assert permission_ids == ["p1", "p2"]
        unassigned_name = db.scalar(
            select(tables["roles"].c.name)
            .join(
                tables["user_roles"],
                tables["user_roles"].c.role_id == tables["roles"].c.id,
            )
            .where(tables["user_roles"].c.user_id == "u-empty")
        )
        assert unassigned_name == "unassigned"

    with pytest.raises(IntegrityError), Session(engine) as db:
        db.execute(
            tables["user_roles"].insert().values(
                id="duplicate", user_id="u-many", role_id="r1", created_at=now
            )
        )
        db.commit()
