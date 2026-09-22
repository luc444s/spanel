from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session, get_settings_dep
from systutor.core.config import Settings
from systutor.kernel.audit.service import record_audit
from systutor.kernel.auth.dependencies import get_current_user
from systutor.kernel.auth.models import User
from systutor.kernel.auth.schemas import LoginRequest, LoginResponse, UserProfile
from systutor.kernel.auth.service import (
    authenticate_user,
    build_user_profile,
    issue_access_token,
    mark_user_logged_in,
)
from systutor.kernel.events.service import record_event

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> LoginResponse:
    user = authenticate_user(db, payload.email, payload.password)

    if user is None:
        record_audit(
            db,
            tenant_id=None,
            branch_id=None,
            actor_user_id=None,
            actor_type="anonymous",
            module="core",
            action="auth.login",
            entity_type="user",
            entity_id=payload.email,
            result="failure",
            correlation_id=getattr(request.state, "correlation_id", None),
            request_id=getattr(request.state, "request_id", None),
            details={"email": payload.email},
        )
        record_event(
            db,
            event_name="core.user.login_failed",
            module="core",
            tenant_id=None,
            branch_id=None,
            actor_user_id=None,
            actor_type="anonymous",
            entity_type="user",
            entity_id=payload.email,
            correlation_id=getattr(request.state, "correlation_id", None),
            causation_id=None,
            payload={"email": payload.email},
            metadata_json={"request_id": getattr(request.state, "request_id", None)},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    mark_user_logged_in(db, user)
    access_token = issue_access_token(settings, user)

    record_audit(
        db,
        tenant_id=user.tenant_id,
        branch_id=user.branch_id,
        actor_user_id=user.id,
        actor_type="user",
        module="core",
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        result="success",
        correlation_id=getattr(request.state, "correlation_id", None),
        request_id=getattr(request.state, "request_id", None),
        details={"email": user.email},
    )
    record_event(
        db,
        event_name="core.user.logged_in",
        module="core",
        tenant_id=user.tenant_id,
        branch_id=user.branch_id,
        actor_user_id=user.id,
        actor_type="user",
        entity_type="user",
        entity_id=user.id,
        correlation_id=getattr(request.state, "correlation_id", None),
        causation_id=None,
        payload={"email": user.email},
        metadata_json={"request_id": getattr(request.state, "request_id", None)},
    )
    db.commit()

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.jwt_access_token_ttl_minutes * 60,
        user=UserProfile.model_validate(build_user_profile(db, user)),
    )


@router.get("/me", response_model=UserProfile)
def me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> UserProfile:
    return UserProfile.model_validate(build_user_profile(db, current_user))
