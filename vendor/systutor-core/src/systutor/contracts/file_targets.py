"""Application-local registry of file targets supplied by host plugins."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class FileTarget:
    container: str
    root: str
    resource_id: str
    # `php` means the container itself has PHP and can run the filemanager
    # script. `volume` means it does not (a static nginx site): the driver
    # mounts this container's volumes in a disposable helper image instead.
    runtime: Literal["php", "volume"] = "php"


FileTargetResolver = Callable[[Session, str, str, bool, tuple[str, ...]], FileTarget | None]


def register_file_target(app: FastAPI, name: str, resolver: FileTargetResolver) -> None:
    providers: dict[str, FileTargetResolver] | None = getattr(
        app.state, "file_target_providers", None
    )
    if providers is None:
        providers = {}
        app.state.file_target_providers = providers
    existing = providers.get(name)
    if existing is not None and (
        existing.__module__, existing.__qualname__
    ) != (resolver.__module__, resolver.__qualname__):
        raise ValueError(f"Duplicate file target provider: {name}")
    providers[name] = resolver


def resolve_file_target(
    app: FastAPI,
    name: str,
    db: Session,
    tenant_id: str,
    resource_id: str,
    write: bool,
    permissions: tuple[str, ...],
) -> FileTarget | None:
    providers: dict[str, FileTargetResolver] = getattr(app.state, "file_target_providers", {})
    resolver = providers.get(name)
    return resolver(db, tenant_id, resource_id, write, permissions) if resolver else None
