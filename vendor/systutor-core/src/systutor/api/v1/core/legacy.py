# ruff: noqa: B008

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session
from systutor.core.lifecycle import bootstrap_app_state
from systutor.kernel.audit.service import record_audit
from systutor.kernel.auth.dependencies import get_current_tenant_context, require_permission
from systutor.kernel.auth.models import User
from systutor.kernel.auth.security import hash_password
from systutor.kernel.auth.service import (
    create_user_for_tenant,
    delete_user_for_tenant,
    get_user_for_tenant,
    list_users_for_tenant,
    update_user_for_tenant,
)
from systutor.kernel.permissions.service import (
    assign_role_to_user,
    create_role_for_tenant,
    delete_role_for_tenant,
    get_permission_by_id,
    get_role_for_tenant,
    list_permissions,
    list_roles_for_tenant,
    remove_role_from_user,
    update_role_for_tenant,
)
from systutor.kernel.plugins.persistent import (
    PluginOperationContext,
    disable_plugin,
    downgrade_plugin,
    enable_plugin,
    install_plugin,
    rollback_plugin,
    uninstall_plugin,
    upgrade_plugin,
)
from systutor.kernel.plugins.service import (
    get_plugin_registry_record_by_plugin_id,
    list_plugin_registry_records,
)
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Branch
from systutor.kernel.tenants.service import (
    TenantScopeError,
    create_branch_for_tenant,
    delete_branch_for_tenant,
    get_audit_log_for_tenant,
    get_branch_for_tenant,
    list_audit_logs_for_tenant,
    list_branches_for_tenant,
    update_branch_for_tenant,
)

router = APIRouter(tags=["core"])

REQUIRE_USERS_READ = Depends(require_permission("core.users.read"))
REQUIRE_USERS_CREATE = Depends(require_permission("core.users.create"))
REQUIRE_USERS_UPDATE = Depends(require_permission("core.users.update"))
REQUIRE_USERS_DELETE = Depends(require_permission("core.users.delete"))
REQUIRE_ROLES_READ = Depends(require_permission("core.roles.read"))
REQUIRE_ROLES_MANAGE = Depends(require_permission("core.roles.manage"))
REQUIRE_BRANCHES_MANAGE = Depends(require_permission("core.branches.manage"))
REQUIRE_AUDIT_READ = Depends(require_permission("core.audit.read"))
REQUIRE_PERMISSION_MANAGE = Depends(require_permission("core.permission.manage"))
REQUIRE_PLUGIN_READ = Depends(require_permission("core.plugin.read"))
REQUIRE_PLUGIN_MANAGE = Depends(require_permission("core.plugin.manage"))


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    branch_id: str | None
    email: str
    full_name: str
    is_active: bool
    is_superadmin: bool
    created_at: datetime
    updated_at: datetime


class UserCreateRequest(BaseModel):
    email: str
    full_name: str
    password: str
    branch_id: str | None = None
    is_active: bool = True


class UserUpdateRequest(BaseModel):
    full_name: str | None = None
    password: str | None = None
    branch_id: str | None = None
    is_active: bool | None = None


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class RoleCreateRequest(BaseModel):
    name: str
    description: str | None = None


class RoleUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class RoleAssignmentRequest(BaseModel):
    role_id: str


class RoleAssignmentRead(BaseModel):
    user_id: str
    role_id: str


class BranchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    name: str
    code: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class BranchCreateRequest(BaseModel):
    name: str
    code: str
    is_active: bool = True


class BranchUpdateRequest(BaseModel):
    name: str | None = None
    code: str | None = None
    is_active: bool | None = None


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    created_at: datetime


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str | None
    branch_id: str | None
    actor_user_id: str | None
    actor_type: str
    module: str
    action: str
    entity_type: str | None
    entity_id: str | None
    result: str
    correlation_id: str | None
    request_id: str | None
    details: dict
    occurred_at: datetime


class PluginRegistryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    plugin_id: str
    name: str
    version: str
    api_version: str
    state: str
    is_enabled: bool
    backend_entrypoint: str | None
    frontend_entrypoint: str | None
    requires_json: list[str]
    permissions_json: list[str]
    events_json: list[str]
    description: str | None
    migration_version: str | None
    installed_at: datetime | None
    enabled_at: datetime | None
    disabled_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class PluginRuntimeMigrationRequest(BaseModel):
    target_revision: str | None = None


def _resolve_branch(
    db: Session,
    *,
    tenant_id: str,
    branch_id: str | None,
) -> Branch | None:
    if branch_id is None:
        return None
    branch = get_branch_for_tenant(db, tenant_id, branch_id)
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid branch for tenant"
        )
    return branch


def _tenant_not_found(entity_name: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{entity_name} not found")


def _handle_integrity_error(exc: IntegrityError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail=str(exc.orig) if exc.orig else str(exc)
    )


def _audit_write(
    db: Session,
    request: Request,
    tenant_context: TenantContext,
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    details: dict,
) -> None:
    record_audit(
        db,
        tenant_id=tenant_context.current_tenant_id,
        branch_id=tenant_context.current_branch_id,
        actor_user_id=tenant_context.current_user_id,
        actor_type="user",
        module="core",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        result="success",
        correlation_id=getattr(request.state, "correlation_id", None),
        request_id=getattr(request.state, "request_id", None),
        details=details,
    )


def _plugin_runtime_audit(
    db: Session,
    request: Request,
    current_user: User,
    *,
    action: str,
    plugin_id: str,
    details: dict,
) -> None:
    record_audit(
        db,
        tenant_id=current_user.tenant_id,
        branch_id=current_user.branch_id,
        actor_user_id=current_user.id,
        actor_type="user",
        module="core",
        action=action,
        entity_type="plugin",
        entity_id=plugin_id,
        result="success",
        correlation_id=getattr(request.state, "correlation_id", None),
        request_id=getattr(request.state, "request_id", None),
        details=details,
    )


def _refresh_plugin_runtime(request: Request, db: Session) -> None:
    bootstrap_app_state(request.app, request.app.state.settings)
    db.expire_all()


def _plugin_operation_context(request: Request, current_user: User) -> PluginOperationContext:
    return PluginOperationContext(
        actor_user_id=current_user.id,
        actor_type="user",
        tenant_id=current_user.tenant_id,
        branch_id=current_user.branch_id,
        correlation_id=getattr(request.state, "correlation_id", None),
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/users", response_model=list[UserRead])
def list_users(
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_USERS_READ,
) -> list[UserRead]:
    return [
        UserRead.model_validate(user)
        for user in list_users_for_tenant(db, tenant_id=tenant_context.current_tenant_id)
    ]


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateRequest,
    request: Request,
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_USERS_CREATE,
) -> UserRead:
    branch = _resolve_branch(
        db, tenant_id=tenant_context.current_tenant_id, branch_id=payload.branch_id
    )
    try:
        user = create_user_for_tenant(
            db,
            tenant_id=tenant_context.current_tenant_id,
            email=str(payload.email),
            full_name=payload.full_name,
            password_hash=hash_password(payload.password),
            branch=branch,
            is_active=payload.is_active,
        )
        _audit_write(
            db,
            request,
            tenant_context,
            action="users.create",
            entity_type="user",
            entity_id=user.id,
            details={"email": user.email},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _handle_integrity_error(exc) from exc
    return UserRead.model_validate(user)


@router.get("/users/{user_id}", response_model=UserRead)
def get_user(
    user_id: str,
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_USERS_READ,
) -> UserRead:
    user = get_user_for_tenant(db, tenant_id=tenant_context.current_tenant_id, user_id=user_id)
    if user is None:
        raise _tenant_not_found("User")
    return UserRead.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserRead)
def update_user(
    user_id: str,
    payload: UserUpdateRequest,
    request: Request,
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_USERS_UPDATE,
) -> UserRead:
    user = get_user_for_tenant(db, tenant_id=tenant_context.current_tenant_id, user_id=user_id)
    if user is None:
        raise _tenant_not_found("User")
    branch_was_provided = "branch_id" in payload.model_fields_set
    branch = None
    if branch_was_provided:
        branch = _resolve_branch(
            db, tenant_id=tenant_context.current_tenant_id, branch_id=payload.branch_id
        )

    try:
        user = update_user_for_tenant(
            db,
            user=user,
            full_name=payload.full_name,
            password_hash=hash_password(payload.password) if payload.password is not None else None,
            branch=branch,
            is_active=payload.is_active,
            branch_was_provided=branch_was_provided,
        )
        _audit_write(
            db,
            request,
            tenant_context,
            action="users.update",
            entity_type="user",
            entity_id=user.id,
            details={"email": user.email},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _handle_integrity_error(exc) from exc
    return UserRead.model_validate(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_user(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_USERS_DELETE,
) -> Response:
    user = get_user_for_tenant(db, tenant_id=tenant_context.current_tenant_id, user_id=user_id)
    if user is None:
        raise _tenant_not_found("User")
    try:
        delete_user_for_tenant(db, user=user)
        _audit_write(
            db,
            request,
            tenant_context,
            action="users.delete",
            entity_type="user",
            entity_id=user_id,
            details={"email": user.email},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _handle_integrity_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/roles", response_model=list[RoleRead])
def list_roles(
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_ROLES_READ,
) -> list[RoleRead]:
    return [
        RoleRead.model_validate(role)
        for role in list_roles_for_tenant(db, tenant_id=tenant_context.current_tenant_id)
    ]


@router.post("/roles", response_model=RoleRead, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreateRequest,
    request: Request,
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_ROLES_MANAGE,
) -> RoleRead:
    try:
        role = create_role_for_tenant(
            db,
            tenant_id=tenant_context.current_tenant_id,
            name=payload.name,
            description=payload.description,
        )
        _audit_write(
            db,
            request,
            tenant_context,
            action="roles.create",
            entity_type="role",
            entity_id=role.id,
            details={"name": role.name},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _handle_integrity_error(exc) from exc
    return RoleRead.model_validate(role)


@router.get("/roles/{role_id}", response_model=RoleRead)
def get_role(
    role_id: str,
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_ROLES_READ,
) -> RoleRead:
    role = get_role_for_tenant(db, tenant_id=tenant_context.current_tenant_id, role_id=role_id)
    if role is None:
        raise _tenant_not_found("Role")
    return RoleRead.model_validate(role)


@router.patch("/roles/{role_id}", response_model=RoleRead)
def update_role(
    role_id: str,
    payload: RoleUpdateRequest,
    request: Request,
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_ROLES_MANAGE,
) -> RoleRead:
    role = get_role_for_tenant(db, tenant_id=tenant_context.current_tenant_id, role_id=role_id)
    if role is None:
        raise _tenant_not_found("Role")
    try:
        role = update_role_for_tenant(
            db, role=role, name=payload.name, description=payload.description
        )
        _audit_write(
            db,
            request,
            tenant_context,
            action="roles.update",
            entity_type="role",
            entity_id=role.id,
            details={"name": role.name},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _handle_integrity_error(exc) from exc
    return RoleRead.model_validate(role)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_role(
    role_id: str,
    request: Request,
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_ROLES_MANAGE,
) -> Response:
    role = get_role_for_tenant(db, tenant_id=tenant_context.current_tenant_id, role_id=role_id)
    if role is None:
        raise _tenant_not_found("Role")
    try:
        delete_role_for_tenant(db, role=role)
        _audit_write(
            db,
            request,
            tenant_context,
            action="roles.delete",
            entity_type="role",
            entity_id=role_id,
            details={"name": role.name},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _handle_integrity_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/users/{user_id}/roles", response_model=RoleAssignmentRead, status_code=status.HTTP_201_CREATED
)
def assign_role(
    user_id: str,
    payload: RoleAssignmentRequest,
    request: Request,
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_ROLES_MANAGE,
) -> RoleAssignmentRead:
    user = get_user_for_tenant(db, tenant_id=tenant_context.current_tenant_id, user_id=user_id)
    if user is None:
        raise _tenant_not_found("User")
    role = get_role_for_tenant(
        db, tenant_id=tenant_context.current_tenant_id, role_id=payload.role_id
    )
    if role is None:
        raise _tenant_not_found("Role")
    try:
        user_role = assign_role_to_user(db, user=user, role=role)
        _audit_write(
            db,
            request,
            tenant_context,
            action="roles.assign",
            entity_type="user_role",
            entity_id=user_role.id,
            details={"user_id": user.id, "role_id": role.id},
        )
        db.commit()
    except (IntegrityError, TenantScopeError) as exc:
        db.rollback()
        if isinstance(exc, IntegrityError):
            raise _handle_integrity_error(exc) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RoleAssignmentRead(user_id=user.id, role_id=role.id)


@router.delete(
    "/users/{user_id}/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def remove_role(
    user_id: str,
    role_id: str,
    request: Request,
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_ROLES_MANAGE,
) -> Response:
    user = get_user_for_tenant(db, tenant_id=tenant_context.current_tenant_id, user_id=user_id)
    if user is None:
        raise _tenant_not_found("User")
    role = get_role_for_tenant(db, tenant_id=tenant_context.current_tenant_id, role_id=role_id)
    if role is None:
        raise _tenant_not_found("Role")
    try:
        removed = remove_role_from_user(db, user=user, role=role)
        if not removed:
            raise _tenant_not_found("User role")
        _audit_write(
            db,
            request,
            tenant_context,
            action="roles.remove",
            entity_type="user_role",
            entity_id=f"{user.id}:{role.id}",
            details={"user_id": user.id, "role_id": role.id},
        )
        db.commit()
    except TenantScopeError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/branches", response_model=list[BranchRead])
def list_branches(
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_BRANCHES_MANAGE,
) -> list[BranchRead]:
    return [
        BranchRead.model_validate(branch)
        for branch in list_branches_for_tenant(db, tenant_id=tenant_context.current_tenant_id)
    ]


@router.post("/branches", response_model=BranchRead, status_code=status.HTTP_201_CREATED)
def create_branch(
    payload: BranchCreateRequest,
    request: Request,
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_BRANCHES_MANAGE,
) -> BranchRead:
    try:
        branch = create_branch_for_tenant(
            db,
            tenant_id=tenant_context.current_tenant_id,
            name=payload.name,
            code=payload.code,
            is_active=payload.is_active,
        )
        _audit_write(
            db,
            request,
            tenant_context,
            action="branches.create",
            entity_type="branch",
            entity_id=branch.id,
            details={"code": branch.code},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _handle_integrity_error(exc) from exc
    return BranchRead.model_validate(branch)


@router.get("/branches/{branch_id}", response_model=BranchRead)
def get_branch(
    branch_id: str,
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_BRANCHES_MANAGE,
) -> BranchRead:
    branch = get_branch_for_tenant(db, tenant_context.current_tenant_id, branch_id)
    if branch is None:
        raise _tenant_not_found("Branch")
    return BranchRead.model_validate(branch)


@router.patch("/branches/{branch_id}", response_model=BranchRead)
def update_branch(
    branch_id: str,
    payload: BranchUpdateRequest,
    request: Request,
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_BRANCHES_MANAGE,
) -> BranchRead:
    branch = get_branch_for_tenant(db, tenant_context.current_tenant_id, branch_id)
    if branch is None:
        raise _tenant_not_found("Branch")
    try:
        branch = update_branch_for_tenant(
            db,
            branch=branch,
            name=payload.name,
            code=payload.code,
            is_active=payload.is_active,
        )
        _audit_write(
            db,
            request,
            tenant_context,
            action="branches.update",
            entity_type="branch",
            entity_id=branch.id,
            details={"code": branch.code},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _handle_integrity_error(exc) from exc
    return BranchRead.model_validate(branch)


@router.delete(
    "/branches/{branch_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
def delete_branch(
    branch_id: str,
    request: Request,
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_BRANCHES_MANAGE,
) -> Response:
    branch = get_branch_for_tenant(db, tenant_context.current_tenant_id, branch_id)
    if branch is None:
        raise _tenant_not_found("Branch")
    try:
        delete_branch_for_tenant(db, branch=branch)
        _audit_write(
            db,
            request,
            tenant_context,
            action="branches.delete",
            entity_type="branch",
            entity_id=branch.id,
            details={"code": branch.code},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _handle_integrity_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/permissions", response_model=list[PermissionRead])
def get_permissions(
    db: Session = Depends(get_db_session),
    _: User = REQUIRE_PERMISSION_MANAGE,
) -> list[PermissionRead]:
    return [PermissionRead.model_validate(permission) for permission in list_permissions(db)]


@router.get("/permissions/{permission_id}", response_model=PermissionRead)
def get_permission(
    permission_id: str,
    db: Session = Depends(get_db_session),
    _: User = REQUIRE_PERMISSION_MANAGE,
) -> PermissionRead:
    permission = get_permission_by_id(db, permission_id=permission_id)
    if permission is None:
        raise _tenant_not_found("Permission")
    return PermissionRead.model_validate(permission)


@router.get("/audit-logs", response_model=list[AuditLogRead])
def list_audit_logs(
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_AUDIT_READ,
) -> list[AuditLogRead]:
    return [
        AuditLogRead.model_validate(log)
        for log in list_audit_logs_for_tenant(db, tenant_context.current_tenant_id)
    ]


@router.get("/audit-logs/{audit_log_id}", response_model=AuditLogRead)
def get_audit_log(
    audit_log_id: str,
    db: Session = Depends(get_db_session),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _: User = REQUIRE_AUDIT_READ,
) -> AuditLogRead:
    log = get_audit_log_for_tenant(
        db, tenant_id=tenant_context.current_tenant_id, audit_log_id=audit_log_id
    )
    if log is None:
        raise _tenant_not_found("Audit log")
    return AuditLogRead.model_validate(log)


@router.get("/plugin-registry", response_model=list[PluginRegistryRead])
def list_plugin_registry(
    db: Session = Depends(get_db_session),
    _: User = REQUIRE_PLUGIN_READ,
) -> list[PluginRegistryRead]:
    return [
        PluginRegistryRead.model_validate(record) for record in list_plugin_registry_records(db)
    ]


@router.get("/plugin-registry/{plugin_id}", response_model=PluginRegistryRead)
def get_plugin_registry_record(
    plugin_id: str,
    db: Session = Depends(get_db_session),
    _: User = REQUIRE_PLUGIN_READ,
) -> PluginRegistryRead:
    record = get_plugin_registry_record_by_plugin_id(db, plugin_id=plugin_id)
    if record is None:
        raise _tenant_not_found("Plugin registry record")
    return PluginRegistryRead.model_validate(record)


@router.get("/plugin-runtime/debug", response_model=list[PluginRegistryRead])
def list_plugin_runtime_debug(
    db: Session = Depends(get_db_session),
    _: User = REQUIRE_PLUGIN_MANAGE,
) -> list[PluginRegistryRead]:
    return [
        PluginRegistryRead.model_validate(record) for record in list_plugin_registry_records(db)
    ]


@router.post("/plugin-runtime/{plugin_id}/install", response_model=PluginRegistryRead)
def install_plugin_runtime(
    plugin_id: str,
    request: Request,
    db: Session = Depends(get_db_session),
    current_user: User = REQUIRE_PLUGIN_MANAGE,
) -> PluginRegistryRead:
    request.app.state.plugin_registry.discover()
    try:
        record = install_plugin(
            db,
            registry=request.app.state.plugin_registry,
            plugin_id=plugin_id,
            context_builder=request.app.state.plugin_runtime.context_builder,
            operation_context=_plugin_operation_context(request, current_user),
        )
    except Exception:
        db.commit()
        _refresh_plugin_runtime(request, db)
        raise
    _plugin_runtime_audit(
        db,
        request,
        current_user,
        action="plugin.install",
        plugin_id=plugin_id,
        details={"migration_version": record.migration_version},
    )
    db.commit()
    _refresh_plugin_runtime(request, db)
    refreshed = get_plugin_registry_record_by_plugin_id(db, plugin_id=plugin_id)
    if refreshed is None:
        raise _tenant_not_found("Plugin registry record")
    return PluginRegistryRead.model_validate(refreshed)


@router.post("/plugin-runtime/{plugin_id}/enable", response_model=PluginRegistryRead)
def enable_plugin_runtime(
    plugin_id: str,
    request: Request,
    db: Session = Depends(get_db_session),
    current_user: User = REQUIRE_PLUGIN_MANAGE,
) -> PluginRegistryRead:
    request.app.state.plugin_registry.discover()
    try:
        enable_plugin(
            db,
            registry=request.app.state.plugin_registry,
            plugin_id=plugin_id,
            context_builder=request.app.state.plugin_runtime.context_builder,
            operation_context=_plugin_operation_context(request, current_user),
        )
    except Exception:
        db.commit()
        _refresh_plugin_runtime(request, db)
        raise
    _plugin_runtime_audit(
        db,
        request,
        current_user,
        action="plugin.enable",
        plugin_id=plugin_id,
        details={},
    )
    db.commit()
    _refresh_plugin_runtime(request, db)
    refreshed = get_plugin_registry_record_by_plugin_id(db, plugin_id=plugin_id)
    if refreshed is None:
        raise _tenant_not_found("Plugin registry record")
    return PluginRegistryRead.model_validate(refreshed)


@router.post("/plugin-runtime/{plugin_id}/disable", response_model=PluginRegistryRead)
def disable_plugin_runtime(
    plugin_id: str,
    request: Request,
    db: Session = Depends(get_db_session),
    current_user: User = REQUIRE_PLUGIN_MANAGE,
) -> PluginRegistryRead:
    request.app.state.plugin_registry.discover()
    try:
        disable_plugin(
            db,
            registry=request.app.state.plugin_registry,
            plugin_id=plugin_id,
            context_builder=request.app.state.plugin_runtime.context_builder,
            operation_context=_plugin_operation_context(request, current_user),
        )
    except Exception:
        db.commit()
        _refresh_plugin_runtime(request, db)
        raise
    _plugin_runtime_audit(
        db,
        request,
        current_user,
        action="plugin.disable",
        plugin_id=plugin_id,
        details={},
    )
    db.commit()
    _refresh_plugin_runtime(request, db)
    refreshed = get_plugin_registry_record_by_plugin_id(db, plugin_id=plugin_id)
    if refreshed is None:
        raise _tenant_not_found("Plugin registry record")
    return PluginRegistryRead.model_validate(refreshed)


@router.post("/plugin-runtime/{plugin_id}/migrate/upgrade", response_model=PluginRegistryRead)
def upgrade_plugin_runtime_migrations(
    plugin_id: str,
    payload: PluginRuntimeMigrationRequest,
    request: Request,
    db: Session = Depends(get_db_session),
    current_user: User = REQUIRE_PLUGIN_MANAGE,
) -> PluginRegistryRead:
    request.app.state.plugin_registry.discover()
    try:
        record = upgrade_plugin(
            db,
            registry=request.app.state.plugin_registry,
            plugin_id=plugin_id,
            target_revision=payload.target_revision,
            operation_context=_plugin_operation_context(request, current_user),
        )
    except Exception:
        db.commit()
        _refresh_plugin_runtime(request, db)
        raise
    _plugin_runtime_audit(
        db,
        request,
        current_user,
        action="plugin.migrate.upgrade",
        plugin_id=plugin_id,
        details={
            "target_revision": payload.target_revision,
            "migration_version": record.migration_version,
        },
    )
    db.commit()
    _refresh_plugin_runtime(request, db)
    refreshed = get_plugin_registry_record_by_plugin_id(db, plugin_id=plugin_id)
    if refreshed is None:
        raise _tenant_not_found("Plugin registry record")
    return PluginRegistryRead.model_validate(refreshed)


@router.post("/plugin-runtime/{plugin_id}/migrate/downgrade", response_model=PluginRegistryRead)
def downgrade_plugin_runtime_migrations(
    plugin_id: str,
    payload: PluginRuntimeMigrationRequest,
    request: Request,
    db: Session = Depends(get_db_session),
    current_user: User = REQUIRE_PLUGIN_MANAGE,
) -> PluginRegistryRead:
    request.app.state.plugin_registry.discover()
    try:
        record = downgrade_plugin(
            db,
            registry=request.app.state.plugin_registry,
            plugin_id=plugin_id,
            target_revision=payload.target_revision,
            operation_context=_plugin_operation_context(request, current_user),
        )
    except Exception:
        db.commit()
        _refresh_plugin_runtime(request, db)
        raise
    _plugin_runtime_audit(
        db,
        request,
        current_user,
        action="plugin.migrate.downgrade",
        plugin_id=plugin_id,
        details={
            "target_revision": payload.target_revision,
            "migration_version": record.migration_version,
        },
    )
    db.commit()
    _refresh_plugin_runtime(request, db)
    refreshed = get_plugin_registry_record_by_plugin_id(db, plugin_id=plugin_id)
    if refreshed is None:
        raise _tenant_not_found("Plugin registry record")
    return PluginRegistryRead.model_validate(refreshed)


@router.post("/plugin-runtime/{plugin_id}/migrate/rollback", response_model=PluginRegistryRead)
def rollback_plugin_runtime_migrations(
    plugin_id: str,
    request: Request,
    db: Session = Depends(get_db_session),
    current_user: User = REQUIRE_PLUGIN_MANAGE,
) -> PluginRegistryRead:
    request.app.state.plugin_registry.discover()
    try:
        record = rollback_plugin(
            db,
            registry=request.app.state.plugin_registry,
            plugin_id=plugin_id,
            operation_context=_plugin_operation_context(request, current_user),
        )
    except Exception:
        db.commit()
        _refresh_plugin_runtime(request, db)
        raise
    _plugin_runtime_audit(
        db,
        request,
        current_user,
        action="plugin.migrate.rollback",
        plugin_id=plugin_id,
        details={"migration_version": record.migration_version},
    )
    db.commit()
    _refresh_plugin_runtime(request, db)
    refreshed = get_plugin_registry_record_by_plugin_id(db, plugin_id=plugin_id)
    if refreshed is None:
        raise _tenant_not_found("Plugin registry record")
    return PluginRegistryRead.model_validate(refreshed)


@router.post("/plugin-runtime/{plugin_id}/uninstall", response_model=PluginRegistryRead)
def uninstall_plugin_runtime(
    plugin_id: str,
    request: Request,
    db: Session = Depends(get_db_session),
    current_user: User = REQUIRE_PLUGIN_MANAGE,
) -> PluginRegistryRead:
    request.app.state.plugin_registry.discover()
    try:
        record = uninstall_plugin(
            db,
            registry=request.app.state.plugin_registry,
            plugin_id=plugin_id,
            context_builder=request.app.state.plugin_runtime.context_builder,
            operation_context=_plugin_operation_context(request, current_user),
        )
    except Exception:
        db.commit()
        _refresh_plugin_runtime(request, db)
        raise
    _plugin_runtime_audit(
        db,
        request,
        current_user,
        action="plugin.uninstall",
        plugin_id=plugin_id,
        details={"migration_version": record.migration_version},
    )
    db.commit()
    _refresh_plugin_runtime(request, db)
    refreshed = get_plugin_registry_record_by_plugin_id(db, plugin_id=plugin_id)
    if refreshed is None:
        raise _tenant_not_found("Plugin registry record")
    return PluginRegistryRead.model_validate(refreshed)
