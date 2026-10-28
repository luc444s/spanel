"""Configuración y dominios administrados por tenant.

El sufijo de sitios y el conjunto explícito de dominios viven en tablas
propias y no se mezclan con `tenants.domain`, que sigue siendo el dominio de
correo primario. La migración del plugin crea las tablas; los modelos existen
para que los servicios y los tests usen SQLAlchemy.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from systutor.core.database import Base
from systutor.kernel.tenants.models import utc_now


class TenantSiteSettings(Base):
    __tablename__ = "tenant_site_settings"

    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        primary_key=True,
    )
    # PostgreSQL permite varios NULL en una columna UNIQUE: la ausencia de
    # sufijo es un estado válido y no compite con otros tenants.
    site_suffix: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class TenantDomain(Base):
    """Dominio administrado por un tenant.

    La PK es el dominio normalizado: dos tenants no pueden poseer el mismo y
    el índice por `tenant_id` responde el conjunto de un tenant.
    """

    __tablename__ = "tenant_domains"

    domain: Mapped[str] = mapped_column(String(255), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
