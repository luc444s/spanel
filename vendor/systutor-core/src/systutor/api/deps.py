from collections.abc import AsyncGenerator, Generator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from systutor.core.config import Settings
from systutor.core.database import async_db_session_scope, db_session_scope
from systutor.core.lifecycle import ensure_async_session_factory, ensure_session_factory
from systutor.kernel.events.bus import EventBus
from systutor.kernel.plugins.runtime import PluginManifestRegistry


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_plugin_registry(request: Request) -> PluginManifestRegistry:
    return request.app.state.plugin_registry


def get_event_bus(request: Request) -> EventBus:
    return request.app.state.event_bus


def get_db_session(request: Request) -> Generator[Session, None, None]:
    session_factory = ensure_session_factory(request.app)
    yield from db_session_scope(session_factory)


async def get_async_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    session_factory = ensure_async_session_factory(request.app)
    async for session in async_db_session_scope(session_factory):
        yield session
