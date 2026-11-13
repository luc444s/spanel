"""enforce exactly one role assignment per tenant user

Revision ID: 20261112_0011
Revises: 20260715_0010
Create Date: 2026-11-12 00:00:00.000000
"""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "20261112_0011"
down_revision = "20260715_0010"
branch_labels = None
depends_on = None


def _id() -> str:
    return str(uuid4())


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)

    duplicate_users = bind.execute(
        sa.text(
            "SELECT user_id FROM user_roles GROUP BY user_id HAVING COUNT(*) > 1"
        )
    ).scalars()
    for user_id in list(duplicate_users):
        tenant_id = bind.execute(
            sa.text("SELECT tenant_id FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        ).scalar_one()
        permission_ids = list(
            bind.execute(
                sa.text(
                    "SELECT DISTINCT rp.permission_id "
                    "FROM role_permissions rp "
                    "JOIN user_roles ur ON ur.role_id = rp.role_id "
                    "WHERE ur.user_id = :user_id"
                ),
                {"user_id": user_id},
            ).scalars()
        )
        role_id = _id()
        bind.execute(
            sa.text(
                "INSERT INTO roles "
                "(id, tenant_id, name, description, is_active, created_at, updated_at) "
                "VALUES (:id, :tenant_id, :name, :description, :active, :created_at, :updated_at)"
            ),
            {
                "id": role_id,
                "tenant_id": tenant_id,
                "name": f"combined_{user_id}",
                "description": "Permission-preserving migration from multiple roles",
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
        )
        for permission_id in permission_ids:
            bind.execute(
                sa.text(
                    "INSERT INTO role_permissions "
                    "(id, role_id, permission_id, created_at) "
                    "VALUES (:id, :role_id, :permission_id, :created_at)"
                ),
                {
                    "id": _id(),
                    "role_id": role_id,
                    "permission_id": permission_id,
                    "created_at": now,
                },
            )
        bind.execute(
            sa.text("DELETE FROM user_roles WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        bind.execute(
            sa.text(
                "INSERT INTO user_roles (id, user_id, role_id, created_at) "
                "VALUES (:id, :user_id, :role_id, :created_at)"
            ),
            {"id": _id(), "user_id": user_id, "role_id": role_id, "created_at": now},
        )

    roleless_users = list(
        bind.execute(
            sa.text(
                "SELECT u.id, u.tenant_id FROM users u "
                "LEFT JOIN user_roles ur ON ur.user_id = u.id "
                "WHERE ur.id IS NULL"
            )
        ).mappings()
    )
    unassigned_by_tenant: dict[str, str] = {}
    for row in roleless_users:
        tenant_id = row["tenant_id"]
        role_id = unassigned_by_tenant.get(tenant_id)
        if role_id is None:
            role_id = bind.execute(
                sa.text(
                    "SELECT id FROM roles WHERE tenant_id = :tenant_id AND name = 'unassigned'"
                ),
                {"tenant_id": tenant_id},
            ).scalar_one_or_none()
            if role_id is None:
                role_id = _id()
                bind.execute(
                    sa.text(
                        "INSERT INTO roles "
                        "(id, tenant_id, name, description, is_active, created_at, updated_at) "
                        "VALUES (:id, :tenant_id, 'unassigned', :description, :active, "
                        ":created_at, :updated_at)"
                    ),
                    {
                        "id": role_id,
                        "tenant_id": tenant_id,
                        "description": "No access assigned",
                        "active": True,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
            else:
                bind.execute(
                    sa.text("DELETE FROM role_permissions WHERE role_id = :role_id"),
                    {"role_id": role_id},
                )
            unassigned_by_tenant[tenant_id] = role_id
        bind.execute(
            sa.text(
                "INSERT INTO user_roles (id, user_id, role_id, created_at) "
                "VALUES (:id, :user_id, :role_id, :created_at)"
            ),
            {"id": _id(), "user_id": row["id"], "role_id": role_id, "created_at": now},
        )

    with op.batch_alter_table("user_roles") as batch_op:
        batch_op.create_unique_constraint("uq_user_role_user", ["user_id"])


def downgrade() -> None:
    with op.batch_alter_table("user_roles") as batch_op:
        batch_op.drop_constraint("uq_user_role_user", type_="unique")
