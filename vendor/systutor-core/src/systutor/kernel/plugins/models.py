from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from systutor.core.database import Base


def new_uuid() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class PluginRegistry(Base):
    __tablename__ = "plugin_registry"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    plugin_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    api_version: Mapped[str] = mapped_column(String(20), nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="validated")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    backend_entrypoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    frontend_entrypoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requires_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    permissions_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    events_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    migration_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )

    @property
    def status(self) -> str:
        return self.state

    @status.setter
    def status(self, value: str) -> None:
        self.state = value

    @property
    def error_message(self) -> str | None:
        return self.last_error

    @error_message.setter
    def error_message(self, value: str | None) -> None:
        self.last_error = value
