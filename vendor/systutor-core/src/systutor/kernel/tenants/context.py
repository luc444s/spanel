from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from systutor.kernel.auth.models import User
from systutor.kernel.permissions.service import list_user_permissions
from systutor.kernel.tenants.service import list_user_warehouse_ids

# The one kernel capability that means "this user operates across tenants".
# It is granted only to the platform administrator role by the seed, never to
# `tenant_admin`, so plugins can ask for a platform-only action without
# checking a role name or a boolean.
PLATFORM_PERMISSION = "core.platform.admin"


@dataclass(slots=True, frozen=True)
class TenantContext:
    current_tenant_id: str
    current_branch_id: str | None
    current_user_id: str
    current_permissions: tuple[str, ...]
    current_warehouse_ids: tuple[str, ...] | None

    def has_permission(self, permission_name: str) -> bool:
        return permission_name in self.current_permissions

    @property
    def is_platform(self) -> bool:
        """True only for the platform administrator, resolved from RBAC."""
        return self.has_permission(PLATFORM_PERMISSION)

    def has_warehouse_access(self, warehouse_id: str) -> bool:
        return self.current_warehouse_ids is None or warehouse_id in self.current_warehouse_ids


def build_tenant_context(db: Session, user: User) -> TenantContext:
    permissions = tuple(list_user_permissions(db, user_id=user.id, tenant_id=user.tenant_id))
    warehouse_ids = tuple(list_user_warehouse_ids(db, tenant_id=user.tenant_id, user_id=user.id))
    return TenantContext(
        current_tenant_id=user.tenant_id,
        current_branch_id=user.branch_id,
        current_user_id=user.id,
        current_permissions=permissions,
        current_warehouse_ids=warehouse_ids or None,
    )
