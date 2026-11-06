"""The ownership row for a WordPress site.

This model is deliberately NOT registered in `systutor.kernel.models`: the
kernel does not know about hosting, and the plugin migration owns the table.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from systutor.core.database import Base
from systutor.kernel.tenants.models import new_uuid, utc_now


class StaticProfile(Base):
    """A curated static-site profile. Only the platform admin writes here.

    The id is the selector the tenant sends when creating a `static` site;
    the image is always platform-owned, never chosen by the tenant.
    """

    __tablename__ = "hosting_static_profiles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    image: Mapped[str] = mapped_column(String(255), nullable=False)
    web_root: Mapped[str] = mapped_column(String(255), nullable=False)
    container_port: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    # `"marker"` (own marker in the served HTML) or `"http_200"` (any 200).
    health_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="marker")
    # `{name: value}` applied with `--env`; the values never leave the table.
    env_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Boot command (`sh -c`); NULL keeps the image's CMD.
    command: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


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
    # `wordpress` or `static`. Existing rows are backfilled by migration 002.
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="wordpress", server_default="wordpress"
    )
    # The static profile the site runs with; NULL for wordpress. Backfilled to
    # the builtin `nginx` by migration 003.
    profile_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("hosting_static_profiles.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
