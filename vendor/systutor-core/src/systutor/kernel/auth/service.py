from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from systutor.kernel.auth.models import User
from systutor.kernel.auth.security import create_access_token, verify_password
from systutor.kernel.permissions.service import list_user_permissions
from systutor.kernel.tenants.models import Branch, Tenant
from systutor.kernel.tenants.service import (
    assign_branch_to_user,
    list_user_warehouse_ids,
    validate_user_branch_scope,
)


def get_user_by_email(db: Session, email: str) -> User | None:
    stmt: Select[tuple[User]] = select(User).where(User.email == email)
    return db.scalar(stmt)


def get_user_by_id(db: Session, user_id: str) -> User | None:
    stmt: Select[tuple[User]] = select(User).where(User.id == user_id)
    return db.scalar(stmt)


def get_user_for_tenant(db: Session, *, tenant_id: str, user_id: str) -> User | None:
    stmt: Select[tuple[User]] = select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    return db.scalar(stmt)


def list_users_for_tenant(db: Session, *, tenant_id: str) -> list[User]:
    stmt: Select[tuple[User]] = (
        select(User).where(User.tenant_id == tenant_id).order_by(User.email.asc())
    )
    return list(db.scalars(stmt))


def create_user_for_tenant(
    db: Session,
    *,
    tenant_id: str,
    email: str,
    full_name: str,
    password_hash: str,
    branch: Branch | None,
    is_active: bool,
) -> User:
    user = User(
        tenant_id=tenant_id,
        branch_id=None,
        email=email,
        full_name=full_name,
        password_hash=password_hash,
        is_active=is_active,
        is_superadmin=False,
    )
    db.add(user)
    db.flush()
    assign_branch_to_user(db, user, branch)
    return user


def update_user_for_tenant(
    db: Session,
    *,
    user: User,
    full_name: str | None = None,
    password_hash: str | None = None,
    branch: Branch | None = None,
    is_active: bool | None = None,
    branch_was_provided: bool = False,
) -> User:
    if full_name is not None:
        user.full_name = full_name
    if password_hash is not None:
        user.password_hash = password_hash
    if is_active is not None:
        user.is_active = is_active
    if branch_was_provided:
        assign_branch_to_user(db, user, branch)
    db.add(user)
    db.flush()
    return user


def delete_user_for_tenant(db: Session, *, user: User) -> None:
    db.delete(user)
    db.flush()


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = get_user_by_email(db, email)
    if user is None or not user.is_active:
        return None
    if not validate_user_branch_scope(db, user):
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def mark_user_logged_in(db: Session, user: User) -> None:
    user.last_login_at = datetime.now(UTC)
    db.add(user)
    db.flush()


def build_user_profile(db: Session, user: User) -> dict[str, object]:
    tenant = db.get(Tenant, user.tenant_id)
    branch = db.get(Branch, user.branch_id) if user.branch_id is not None else None

    return {
        "id": user.id,
        "tenant_id": user.tenant_id,
        "tenant_name": tenant.name if tenant is not None else "",
        "branch_id": user.branch_id,
        "branch_name": branch.name if branch is not None else None,
        "email": user.email,
        "full_name": user.full_name,
        "is_active": user.is_active,
        "is_superadmin": user.is_superadmin,
        "category": user.category,
        "permissions": list_user_permissions(db, user_id=user.id, tenant_id=user.tenant_id),
        "warehouse_ids": list_user_warehouse_ids(db, tenant_id=user.tenant_id, user_id=user.id),
    }


def issue_access_token(settings, user: User) -> str:
    return create_access_token(
        settings=settings,
        subject=user.id,
        email=user.email,
        tenant_id=user.tenant_id,
        branch_id=user.branch_id,
        is_superadmin=user.is_superadmin,
    )
