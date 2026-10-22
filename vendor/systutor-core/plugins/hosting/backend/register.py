from __future__ import annotations

from plugins.hosting.backend.router import router
from plugins.hosting.backend.settings import register_hosting_settings
from systutor.sdk.context import PluginContext


def register(context: PluginContext) -> None:
    register_hosting_settings()
    context.register_router(router)
