"""Create `tenant_site_settings` and backfill the initial suffix.

Additive and reversible: `upgrade` only creates the new table (plus its rows)
and `downgrade` only drops it. `tenants`, `hosting_sites` and proxy YAML are
never touched.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Table,
    select,
)

from systutor.contracts.site_namespace import default_suffix_from_domain

revision = "001_tenant_site_settings"

metadata = MetaData()

# Reference-only table: the FK target already exists in the kernel schema. This
# migration reads it for the backfill but never creates or modifies it.
tenants = Table(
    "tenants",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("domain", String(255)),
)

tenant_site_settings = Table(
    "tenant_site_settings",
    metadata,
    Column("tenant_id", String(36), ForeignKey("tenants.id"), primary_key=True),
    Column("site_suffix", String(255), nullable=True, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def _backfill(db) -> None:
    now = datetime.now(UTC)
    existing = set(db.scalars(select(tenant_site_settings.c.tenant_id)))
    rows = db.execute(select(tenants.c.id, tenants.c.domain)).all()
    for tenant_id, domain in rows:
        if tenant_id in existing:
            continue
        try:
            suffix = default_suffix_from_domain(domain)
        except ValueError:
            # Un dominio histórico inválido no debe romper la migración: el
            # tenant conserva la ausencia de sufijo hasta que se configure.
            suffix = None
        db.execute(
            tenant_site_settings.insert().values(
                tenant_id=tenant_id,
                site_suffix=suffix,
                created_at=now,
                updated_at=now,
            )
        )
    db.flush()


def upgrade(db) -> None:
    tenant_site_settings.create(bind=db.get_bind(), checkfirst=True)
    _backfill(db)


def downgrade(db) -> None:
    tenant_site_settings.drop(bind=db.get_bind(), checkfirst=True)
