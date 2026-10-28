"""Create `tenant_domains` and backfill explicit domain ownership.

Additive and idempotent: `upgrade` creates the table when missing and seeds one
row per tenant domain and per site primary domain. A domain already claimed
(the first one by `container_name`) wins; invalid historical values are
skipped. `downgrade` drops only `tenant_domains`, never `tenants` or
`hosting_sites`.
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
    inspect,
    select,
)

from systutor.contracts.domain_namespace import normalize_domain_name

revision = "002_tenant_domains"

metadata = MetaData()

# Reference-only tables: the real schema already exists. They are read for the
# backfill but never created or modified here.
tenants = Table(
    "tenants",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("domain", String(255)),
)

hosting_sites = Table(
    "hosting_sites",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(36), nullable=False),
    Column("container_name", String(255), nullable=False),
    Column("domain", String(255)),
)

tenant_domains = Table(
    "tenant_domains",
    metadata,
    Column("domain", String(255), primary_key=True),
    Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def _normalize(value: str | None) -> str | None:
    try:
        return normalize_domain_name(value)
    except ValueError:
        # Un dominio histórico inválido no debe romper la migración.
        return None


def _insert(db, *, domain: str, tenant_id: str, now: datetime) -> None:
    db.execute(
        tenant_domains.insert().values(
            domain=domain,
            tenant_id=tenant_id,
            created_at=now,
            updated_at=now,
        )
    )


def _backfill(db) -> None:
    now = datetime.now(UTC)
    bind = db.get_bind()
    # La inspección abre su propia conexión; hacerla antes de escribir evita
    # descartar las filas pendientes en SQLite.
    has_sites = inspect(bind).has_table("hosting_sites")
    existing = set(db.scalars(select(tenant_domains.c.domain)))
    tenant_ids: set[str] = set()

    for tenant_id, domain in db.execute(
        select(tenants.c.id, tenants.c.domain).order_by(tenants.c.id)
    ).all():
        tenant_ids.add(tenant_id)
        normalized = _normalize(domain)
        if normalized is None or normalized in existing:
            continue
        _insert(db, domain=normalized, tenant_id=tenant_id, now=now)
        existing.add(normalized)

    if not has_sites:
        return

    rows = db.execute(
        select(
            hosting_sites.c.tenant_id,
            hosting_sites.c.container_name,
            hosting_sites.c.domain,
        ).order_by(hosting_sites.c.container_name.asc())
    ).all()
    for tenant_id, _container_name, domain in rows:
        if tenant_id not in tenant_ids:
            # Una fila huérfana no puede violar la FK de `tenant_domains`.
            continue
        normalized = _normalize(domain)
        if normalized is None or normalized in existing:
            continue
        _insert(db, domain=normalized, tenant_id=tenant_id, now=now)
        existing.add(normalized)


def upgrade(db) -> None:
    tenant_domains.create(bind=db.get_bind(), checkfirst=True)
    _backfill(db)


def downgrade(db) -> None:
    tenant_domains.drop(bind=db.get_bind(), checkfirst=True)
