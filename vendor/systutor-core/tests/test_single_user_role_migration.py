from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError


def test_migration_preserves_permissions_and_assigns_roleless_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "single-role.sqlite"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("SYSTUTOR_DATABASE_URL", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "20260715_0010")

    engine = sa.create_engine(url)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO tenants (id, name, slug, is_active, created_at, updated_at) "
                "VALUES ('t1', 'Tenant', 'tenant', 1, :now, :now)"
            ),
            {"now": now},
        )
        for user_id in ("u-many", "u-empty"):
            connection.execute(
                sa.text(
                    "INSERT INTO users "
                    "(id, tenant_id, email, full_name, password_hash, is_active, "
                    "is_superadmin, created_at, updated_at) "
                    "VALUES (:id, 't1', :email, :name, 'hash', 1, 0, :now, :now)"
                ),
                {"id": user_id, "email": f"{user_id}@example.com", "name": user_id, "now": now},
            )
        for role_id in ("r1", "r2"):
            connection.execute(
                sa.text(
                    "INSERT INTO roles "
                    "(id, tenant_id, name, is_active, created_at, updated_at) "
                    "VALUES (:id, 't1', :name, 1, :now, :now)"
                ),
                {"id": role_id, "name": role_id, "now": now},
            )
        for permission_id in ("p1", "p2"):
            connection.execute(
                sa.text(
                    "INSERT INTO permissions (id, name, created_at) "
                    "VALUES (:id, :name, :now)"
                ),
                {"id": permission_id, "name": permission_id, "now": now},
            )
        for index, (role_id, permission_id) in enumerate((("r1", "p1"), ("r2", "p2"))):
            connection.execute(
                sa.text(
                    "INSERT INTO role_permissions (id, role_id, permission_id, created_at) "
                    "VALUES (:id, :role_id, :permission_id, :now)"
                ),
                {
                    "id": f"rp{index}",
                    "role_id": role_id,
                    "permission_id": permission_id,
                    "now": now,
                },
            )
            connection.execute(
                sa.text(
                    "INSERT INTO user_roles (id, user_id, role_id, created_at) "
                    "VALUES (:id, 'u-many', :role_id, :now)"
                ),
                {"id": f"ur{index}", "role_id": role_id, "now": now},
            )

    command.upgrade(config, "head")

    with engine.begin() as connection:
        assignments = connection.execute(
            sa.text("SELECT user_id, role_id FROM user_roles ORDER BY user_id")
        ).mappings().all()
        assert [row["user_id"] for row in assignments] == ["u-empty", "u-many"]
        combined_role = next(row["role_id"] for row in assignments if row["user_id"] == "u-many")
        permissions = connection.execute(
            sa.text(
                "SELECT permission_id FROM role_permissions WHERE role_id = :role_id "
                "ORDER BY permission_id"
            ),
            {"role_id": combined_role},
        ).scalars().all()
        assert permissions == ["p1", "p2"]
        unassigned_name = connection.execute(
            sa.text(
                "SELECT r.name FROM roles r JOIN user_roles ur ON ur.role_id = r.id "
                "WHERE ur.user_id = 'u-empty'"
            )
        ).scalar_one()
        assert unassigned_name == "unassigned"

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO user_roles (id, user_id, role_id, created_at) "
                "VALUES ('duplicate', 'u-many', 'r1', :now)"
            ),
            {"now": now},
        )
