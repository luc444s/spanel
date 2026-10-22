"""The ownership row for a WordPress site.

This model is deliberately NOT registered in `systutor.kernel.models`: the
kernel does not know about hosting, and the plugin migration owns the table.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from systutor.core.database import Base
from systutor.kernel.tenants.models import new_uuid, utc_now


class HostingSite(Base):
    __tablename__ = "hosting_sites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    # The container is unique across the machine: two tenants can never own the
    # same WordPress, and the unique constraint is the last line of defence.
    container_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # PostgreSQL allows several NULLs in a unique column, so sites without a
    # domain do not collide with each other.
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
