"""Shared Apps audit helper."""

from __future__ import annotations

from sqlalchemy.orm import Session

from systutor.kernel.audit.service import record_audit


def audit(db: Session, action: str, *, tenant_id: str, actor: str | None,
          entity_id: str | None, result: str = "success", details: dict | None = None) -> None:
    record_audit(
        db, tenant_id=tenant_id, branch_id=None, actor_user_id=actor,
        actor_type="user" if actor else "system", module="apps", action=action,
        entity_type="application", entity_id=entity_id, result=result,
        correlation_id=None, request_id=None, details=details or {},
    )
