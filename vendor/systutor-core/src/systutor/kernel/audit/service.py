from __future__ import annotations

from sqlalchemy.orm import Session

from systutor.kernel.audit.models import AuditLog
from systutor.kernel.tenants.service import resolve_tenant_scope


def record_audit(
    db: Session,
    *,
    tenant_id: str | None,
    branch_id: str | None,
    actor_user_id: str | None,
    actor_type: str,
    module: str,
    action: str,
    entity_type: str | None,
    entity_id: str | None,
    result: str,
    correlation_id: str | None,
    request_id: str | None,
    details: dict,
) -> AuditLog:
    effective_tenant_id, effective_branch_id = resolve_tenant_scope(
        db,
        tenant_id=tenant_id,
        branch_id=branch_id,
        actor_user_id=actor_user_id,
    )
    audit = AuditLog(
        tenant_id=effective_tenant_id,
        branch_id=effective_branch_id,
        actor_user_id=actor_user_id,
        actor_type=actor_type,
        module=module,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        result=result,
        correlation_id=correlation_id,
        request_id=request_id,
        details=details,
    )
    db.add(audit)
    db.flush()
    return audit
