"""The only way an endpoint reaches a site: an owned row or 404.

A site of another tenant and a site that does not exist produce the same
answer, so a 403 can never confirm that somebody else's resource exists.
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session
from systutor.core.errors import NotFoundError
from systutor.kernel.auth.dependencies import get_current_tenant_context
from systutor.kernel.tenants.context import TenantContext

from .models import HostingSite


def get_owned_site(
    site_id: str,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    db: Session = Depends(get_db_session),
) -> HostingSite:
    """Load a row by id AND tenant. The tenant comes from the kernel, never the URL."""
    stmt: Select[tuple[HostingSite]] = select(HostingSite).where(
        HostingSite.id == site_id,
        HostingSite.tenant_id == tenant_context.current_tenant_id,
    )
    site = db.scalar(stmt)
    if site is None:
        # Same text for "missing" and "somebody else's" on purpose.
        raise NotFoundError("Site not found")
    return site
