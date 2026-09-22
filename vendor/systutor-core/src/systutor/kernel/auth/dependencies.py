from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session, get_settings_dep
from systutor.core.config import Settings
from systutor.core.errors import AuthenticationError
from systutor.kernel.audit.service import record_audit
from systutor.kernel.auth.models import User
from systutor.kernel.auth.security import decode_access_token
from systutor.kernel.auth.service import get_user_by_id
from systutor.kernel.tenants.context import TenantContext, build_tenant_context
from systutor.kernel.tenants.service import validate_user_branch_scope

bearer_scheme = HTTPBearer(auto_error=False)


def _require_string_claim(payload: dict[str, object], claim_name: str) -> str:
    value = payload.get(claim_name)
    if not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token {claim_name}",
        )
    return value


def _require_optional_string_claim(payload: dict[str, object], claim_name: str) -> str | None:
    value = payload.get(claim_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token {claim_name}",
        )
    return value


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing credentials")

    try:
        payload = decode_access_token(credentials.credentials, settings)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc

    subject = _require_string_claim(payload, "sub")
    token_email = _require_string_claim(payload, "email")
    token_tenant_id = _require_string_claim(payload, "tenant_id")
    token_branch_id = _require_optional_string_claim(payload, "branch_id")
    token_is_superadmin = payload.get("is_superadmin")
    if not isinstance(token_is_superadmin, bool):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token is_superadmin",
        )

    user = get_user_by_id(db, subject)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not available")
    if user.email != token_email or user.tenant_id != token_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token does not match user",
        )
    if user.branch_id != token_branch_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token branch mismatch",
        )
    if user.is_superadmin != token_is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token privilege mismatch",
        )
    if not validate_user_branch_scope(db, user):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User branch not available",
        )

    request.state.current_user_id = user.id
    request.state.current_tenant_id = user.tenant_id
    request.state.current_branch_id = user.branch_id
    request.state.is_superadmin = user.is_superadmin
    return user


def get_current_tenant_context(
    request: Request,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> TenantContext:
    existing_context = getattr(request.state, "tenant_context", None)
    if isinstance(existing_context, TenantContext):
        return existing_context

    tenant_context = build_tenant_context(db, current_user)
    request.state.tenant_context = tenant_context
    request.state.current_permissions = list(tenant_context.current_permissions)
    return tenant_context


def require_authenticated_tenant(
    tenant_context: TenantContext = Depends(get_current_tenant_context),
) -> TenantContext:
    return tenant_context


def get_current_tenant_id(
    tenant_context: TenantContext = Depends(get_current_tenant_context),
) -> str:
    return tenant_context.current_tenant_id


def get_current_branch_id(
    tenant_context: TenantContext = Depends(get_current_tenant_context),
) -> str | None:
    return tenant_context.current_branch_id


def require_permission(permission_name: str) -> Callable[..., User]:
    def dependency(
        request: Request,
        db: Session = Depends(get_db_session),
        current_user: User = Depends(get_current_user),
        tenant_context: TenantContext = Depends(get_current_tenant_context),
    ) -> User:
        if tenant_context.has_permission(permission_name):
            return current_user

        record_audit(
            db,
            tenant_id=tenant_context.current_tenant_id,
            branch_id=tenant_context.current_branch_id,
            actor_user_id=tenant_context.current_user_id,
            actor_type="user",
            module="core",
            action="permission.denied",
            entity_type="permission",
            entity_id=permission_name,
            result="denied",
            correlation_id=getattr(request.state, "correlation_id", None),
            request_id=getattr(request.state, "request_id", None),
            details={
                "permission": permission_name,
                "tenant_id": tenant_context.current_tenant_id,
            },
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    return dependency


def require_any_permission(*permission_names: str) -> Callable[..., User]:
    def dependency(
        request: Request,
        db: Session = Depends(get_db_session),
        current_user: User = Depends(get_current_user),
        tenant_context: TenantContext = Depends(get_current_tenant_context),
    ) -> User:
        if tenant_context.is_superadmin or any(
            tenant_context.has_permission(permission_name) for permission_name in permission_names
        ):
            return current_user

        record_audit(
            db,
            tenant_id=tenant_context.current_tenant_id,
            branch_id=tenant_context.current_branch_id,
            actor_user_id=tenant_context.current_user_id,
            actor_type="user",
            module="core",
            action="permission.denied",
            entity_type="permission",
            entity_id=",".join(permission_names),
            result="denied",
            correlation_id=getattr(request.state, "correlation_id", None),
            request_id=getattr(request.state, "request_id", None),
            details={
                "permissions": list(permission_names),
                "tenant_id": tenant_context.current_tenant_id,
            },
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    return dependency
