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
