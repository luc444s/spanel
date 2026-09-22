from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from systutor.kernel.auth.models import User
from systutor.kernel.permissions.service import list_user_permissions
from systutor.kernel.tenants.service import list_user_warehouse_ids


@dataclass(slots=True, frozen=True)
class TenantContext:
    current_tenant_id: str
    current_branch_id: str | None
    current_user_id: str
    current_permissions: tuple[str, ...]
    current_warehouse_ids: tuple[str, ...] | None
    is_superadmin: bool

    def has_permission(self, permission_name: str) -> bool:
        return self.is_superadmin or permission_name in self.current_permissions

    def has_warehouse_access(self, warehouse_id: str) -> bool:
        return (
            self.is_superadmin
            or self.current_warehouse_ids is None
            or warehouse_id in self.current_warehouse_ids
        )


def build_tenant_context(db: Session, user: User) -> TenantContext:
    permissions = tuple(list_user_permissions(db, user_id=user.id, tenant_id=user.tenant_id))
    warehouse_ids = tuple(list_user_warehouse_ids(db, tenant_id=user.tenant_id, user_id=user.id))
    return TenantContext(
        current_tenant_id=user.tenant_id,
        current_branch_id=user.branch_id,
        current_user_id=user.id,
        current_permissions=permissions,
        current_warehouse_ids=warehouse_ids or None,
        is_superadmin=user.is_superadmin,
    )
