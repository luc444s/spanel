from __future__ import annotations

from plugins.tenant.backend.router import router
from systutor.sdk.context import PluginContext


def register(context: PluginContext) -> None:
    context.register_router(router)
