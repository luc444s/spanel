"""Normalize tenant users to one role and enforce it at the database boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Index, MetaData, delete, func, inspect, select

revision = "003_single_user_role"


def _id() -> str:
    return str(uuid4())


def _has_unique_user_assignment(bind) -> bool:
    inspector = inspect(bind)
    constraints = inspector.get_unique_constraints("user_roles")
    if any(item.get("column_names") == ["user_id"] for item in constraints):
        return True
    return any(
        item.get("unique") and item.get("column_names") == ["user_id"]
        for item in inspector.get_indexes("user_roles")
    )


def upgrade(db) -> None:
    bind = db.connection()
    inspector = inspect(bind)
    required = {"users", "roles", "permissions", "role_permissions", "user_roles"}
    if not required.issubset(inspector.get_table_names()):
        return

    metadata = MetaData()
    metadata.reflect(bind, only=sorted(required))
    users = metadata.tables["users"]
    roles = metadata.tables["roles"]
    role_permissions = metadata.tables["role_permissions"]
    user_roles = metadata.tables["user_roles"]
    now = datetime.now(UTC)

    duplicate_user_ids = list(
        db.scalars(
            select(user_roles.c.user_id)
            .group_by(user_roles.c.user_id)
            .having(func.count(user_roles.c.id) > 1)
        )
    )
    for user_id in duplicate_user_ids:
        tenant_id = db.scalar(select(users.c.tenant_id).where(users.c.id == user_id))
        permission_ids = list(
            db.scalars(
                select(role_permissions.c.permission_id)
                .join(user_roles, user_roles.c.role_id == role_permissions.c.role_id)
                .where(user_roles.c.user_id == user_id)
                .distinct()
            )
        )
        role_id = _id()
        db.execute(
            roles.insert().values(
                id=role_id,
                tenant_id=tenant_id,
                name=f"combined_{user_id}",
                description="Permission-preserving runtime migration from multiple roles",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        for permission_id in permission_ids:
            db.execute(
                role_permissions.insert().values(
                    id=_id(),
                    role_id=role_id,
                    permission_id=permission_id,
                    created_at=now,
                )
            )
        db.execute(delete(user_roles).where(user_roles.c.user_id == user_id))
        db.execute(
            user_roles.insert().values(
                id=_id(), user_id=user_id, role_id=role_id, created_at=now
            )
        )

    assigned_users = select(user_roles.c.user_id)
    roleless_rows = list(
        db.execute(
            select(users.c.id, users.c.tenant_id).where(users.c.id.not_in(assigned_users))
        )
    )
    unassigned_by_tenant: dict[str, str] = {}
    for user_id, tenant_id in roleless_rows:
        role_id = unassigned_by_tenant.get(tenant_id)
        if role_id is None:
            role_id = db.scalar(
                select(roles.c.id).where(
                    roles.c.tenant_id == tenant_id,
                    roles.c.name == "unassigned",
                )
            )
            if role_id is None:
                role_id = _id()
                db.execute(
                    roles.insert().values(
                        id=role_id,
                        tenant_id=tenant_id,
                        name="unassigned",
                        description="No access assigned",
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                db.execute(
                    delete(role_permissions).where(role_permissions.c.role_id == role_id)
                )
            unassigned_by_tenant[tenant_id] = role_id
        db.execute(
            user_roles.insert().values(
                id=_id(), user_id=user_id, role_id=role_id, created_at=now
            )
        )

    db.flush()
    if not _has_unique_user_assignment(bind):
        Index("uq_user_role_user", user_roles.c.user_id, unique=True).create(bind=bind)


def downgrade(db) -> None:
    bind = db.connection()
    indexes = inspect(bind).get_indexes("user_roles")
    if any(index.get("name") == "uq_user_role_user" for index in indexes):
        metadata = MetaData()
        metadata.reflect(bind, only=["user_roles"])
        Index("uq_user_role_user", metadata.tables["user_roles"].c.user_id).drop(bind=bind)
