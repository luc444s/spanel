"""Persistencia mínima de rutas del plugin de dominios.

Las rutas viven en Dominios porque son su responsabilidad: ownership, path,
TLS y publicación. Apps sólo aporta la identidad estable del sitio mediante el
contrato `domain_ownership`; nunca guarda contenedores efímeros.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from systutor.core.database import Base
from systutor.kernel.auth.models import utc_now


def new_id() -> str:
    return str(uuid4())


class DomainRoute(Base):
    __tablename__ = "domains_routes"
    __table_args__ = (UniqueConstraint("tenant_id", "domain", "path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    path: Mapped[str] = mapped_column(String(255))
    internal_path: Mapped[str] = mapped_column(String(255), default="/")
    strip_path: Mapped[bool] = mapped_column(Boolean, default=False)
    port: Mapped[int] = mapped_column(Integer)
    entrypoints: Mapped[list] = mapped_column(JSON, default=list)
    https: Mapped[bool] = mapped_column(Boolean, default=True)
    middlewares: Mapped[list] = mapped_column(JSON, default=list)
    site_id: Mapped[str] = mapped_column(String(504), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ObservedDomain(Base):
    """Rebuildable observation of one domain published by Traefik."""

    __tablename__ = "domains_observed"
    __table_args__ = (UniqueConstraint("generation", "domain"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(String(504), index=True)
    container: Mapped[str] = mapped_column(String(255))
    resolver: Mapped[str] = mapped_column(String(255))
    source_file: Mapped[str] = mapped_column(String(255))
    document_hash: Mapped[str] = mapped_column(String(64))
    generation: Mapped[str] = mapped_column(String(36), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DomainSyncState(Base):
    """Singleton pointer to the last complete observed generation."""

    __tablename__ = "domains_sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    active_generation: Mapped[str | None] = mapped_column(String(36), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    domain_count: Mapped[int] = mapped_column(Integer, default=0)
    conflict_count: Mapped[int] = mapped_column(Integer, default=0)
    orphan_count: Mapped[int] = mapped_column(Integer, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class DnsZoneOwnership(Base):
    """PostgreSQL ownership authority for one PowerDNS zone."""

    __tablename__ = "domains_dns_zones"
    __table_args__ = (UniqueConstraint("zone_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    zone_name: Mapped[str] = mapped_column(String(253), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="Native")
    observed_version: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DnsPublicSettings(Base):
    """Singleton public nameserver contract (never stores URL or key)."""

    __tablename__ = "domains_dns_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    ns1_hostname: Mapped[str] = mapped_column(String(253))
    ns2_hostname: Mapped[str] = mapped_column(String(253))
    ns1_ipv4: Mapped[str | None] = mapped_column(String(45), nullable=True)
    ns1_ipv6: Mapped[str | None] = mapped_column(String(45), nullable=True)
    ns2_ipv4: Mapped[str | None] = mapped_column(String(45), nullable=True)
    ns2_ipv6: Mapped[str | None] = mapped_column(String(45), nullable=True)
    soa_contact: Mapped[str] = mapped_column(String(253))
    default_ttl: Mapped[int] = mapped_column(Integer, default=300)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
