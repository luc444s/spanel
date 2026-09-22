from __future__ import annotations

from sqlalchemy import Select, delete, select
from sqlalchemy.orm import Session

from systutor.kernel.audit.models import AuditLog
from systutor.kernel.auth.models import User
from systutor.kernel.events.models import EventLog, EventOutbox
from systutor.kernel.tenants.models import Branch, Tenant, UserContextClaim


class TenantScopeError(ValueError):
    """Raised when tenant or branch scope is inconsistent."""


def get_tenant_by_id(db: Session, tenant_id: str) -> Tenant | None:
    stmt: Select[tuple[Tenant]] = select(Tenant).where(Tenant.id == tenant_id)
    return db.scalar(stmt)


def get_branch_by_id(db: Session, branch_id: str) -> Branch | None:
    stmt: Select[tuple[Branch]] = select(Branch).where(Branch.id == branch_id)
    return db.scalar(stmt)


def get_branch_for_tenant(db: Session, tenant_id: str, branch_id: str) -> Branch | None:
    stmt: Select[tuple[Branch]] = select(Branch).where(
        Branch.id == branch_id,
        Branch.tenant_id == tenant_id,
    )
    return db.scalar(stmt)


def list_branches_for_tenant(db: Session, *, tenant_id: str) -> list[Branch]:
    stmt: Select[tuple[Branch]] = (
        select(Branch).where(Branch.tenant_id == tenant_id).order_by(Branch.code.asc())
    )
    return list(db.scalars(stmt))


def create_branch_for_tenant(
    db: Session,
    *,
    tenant_id: str,
    name: str,
    code: str,
    is_active: bool,
) -> Branch:
    branch = Branch(tenant_id=tenant_id, name=name, code=code, is_active=is_active)
    db.add(branch)
    db.flush()
    return branch


def update_branch_for_tenant(
    db: Session,
    *,
    branch: Branch,
    name: str | None = None,
    code: str | None = None,
    is_active: bool | None = None,
) -> Branch:
    if name is not None:
        branch.name = name
    if code is not None:
        branch.code = code
    if is_active is not None:
        branch.is_active = is_active
    db.add(branch)
    db.flush()
    return branch


def delete_branch_for_tenant(db: Session, *, branch: Branch) -> None:
    db.delete(branch)
    db.flush()


def get_role_ids_for_tenant(db: Session, tenant_id: str) -> list[str]:
    from systutor.kernel.permissions.models import Role

    stmt: Select[tuple[str]] = select(Role.id).where(Role.tenant_id == tenant_id)
    return list(db.scalars(stmt))


def validate_user_branch_scope(db: Session, user: User) -> bool:
    if user.branch_id is None:
        return True
    return get_branch_for_tenant(db, user.tenant_id, user.branch_id) is not None


def assign_branch_to_user(db: Session, user: User, branch: Branch | None) -> User:
    if branch is not None and branch.tenant_id != user.tenant_id:
        raise TenantScopeError("Branch does not belong to the user's tenant")

    user.branch_id = branch.id if branch is not None else None
    db.add(user)
    db.flush()
    return user


def list_user_claim_values(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    claim_type: str,
) -> list[str]:
    stmt: Select[tuple[str]] = (
        select(UserContextClaim.claim_value)
        .where(
            UserContextClaim.tenant_id == tenant_id,
            UserContextClaim.user_id == user_id,
            UserContextClaim.claim_type == claim_type,
        )
        .order_by(UserContextClaim.claim_value.asc())
    )
    return list(db.scalars(stmt))


def list_user_warehouse_ids(db: Session, *, tenant_id: str, user_id: str) -> list[str]:
    return list_user_claim_values(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        claim_type="warehouse_id",
    )


def replace_user_claim_values(
    db: Session,
    *,
    user: User,
    claim_type: str,
    values: list[str],
) -> list[str]:
    normalized = sorted({value.strip() for value in values if value.strip()})
    db.execute(
        delete(UserContextClaim).where(
            UserContextClaim.user_id == user.id,
            UserContextClaim.claim_type == claim_type,
        )
    )
    db.flush()

    for value in normalized:
        db.add(
            UserContextClaim(
                tenant_id=user.tenant_id,
                user_id=user.id,
                claim_type=claim_type,
                claim_value=value,
            )
        )
    db.flush()
    return normalized


def replace_user_warehouse_ids(db: Session, *, user: User, warehouse_ids: list[str]) -> list[str]:
    return replace_user_claim_values(
        db,
        user=user,
        claim_type="warehouse_id",
        values=warehouse_ids,
    )


def resolve_tenant_scope(
    db: Session,
    *,
    tenant_id: str | None,
    branch_id: str | None,
    actor_user_id: str | None,
) -> tuple[str | None, str | None]:
    effective_tenant_id = tenant_id
    effective_branch_id = branch_id

    actor = db.get(User, actor_user_id) if actor_user_id is not None else None
    if actor is not None:
        if effective_tenant_id is None:
            effective_tenant_id = actor.tenant_id
        elif effective_tenant_id != actor.tenant_id:
            raise TenantScopeError("Actor tenant does not match the requested tenant")

        if not validate_user_branch_scope(db, actor):
            raise TenantScopeError("Actor branch does not belong to the actor tenant")

        if effective_branch_id is None:
            effective_branch_id = actor.branch_id
        elif actor.branch_id is not None and effective_branch_id != actor.branch_id:
            raise TenantScopeError("Actor branch does not match the requested branch")

    if effective_branch_id is not None:
        branch = get_branch_by_id(db, effective_branch_id)
        if branch is None:
            raise TenantScopeError("Branch not found")
        if effective_tenant_id is None:
            effective_tenant_id = branch.tenant_id
        elif branch.tenant_id != effective_tenant_id:
            raise TenantScopeError("Branch does not belong to the requested tenant")

    return effective_tenant_id, effective_branch_id


def list_audit_logs_for_tenant(db: Session, tenant_id: str) -> list[AuditLog]:
    stmt: Select[tuple[AuditLog]] = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
    return list(db.scalars(stmt))


def get_audit_log_for_tenant(db: Session, *, tenant_id: str, audit_log_id: str) -> AuditLog | None:
    stmt: Select[tuple[AuditLog]] = select(AuditLog).where(
        AuditLog.id == audit_log_id,
        AuditLog.tenant_id == tenant_id,
    )
    return db.scalar(stmt)


def list_event_logs_for_tenant(db: Session, tenant_id: str) -> list[EventLog]:
    stmt: Select[tuple[EventLog]] = select(EventLog).where(EventLog.tenant_id == tenant_id)
    return list(db.scalars(stmt))


def list_outbox_events_for_tenant(db: Session, tenant_id: str) -> list[EventOutbox]:
    stmt: Select[tuple[EventOutbox]] = select(EventOutbox).where(EventOutbox.tenant_id == tenant_id)
    return list(db.scalars(stmt))
