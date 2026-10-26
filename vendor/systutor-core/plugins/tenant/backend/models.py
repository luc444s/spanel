"""Configuración de sitios por tenant.

El sufijo vive en su propia tabla y no se mezcla con `tenants.domain`, que
sigue siendo el dominio de correo. La migración del plugin crea la tabla; el
modelo existe para que los servicios y los tests usen SQLAlchemy.
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
