from __future__ import annotations

from systutor.sdk.context import PluginContext

from plugins.mail.backend.router import router
from plugins.mail.backend.settings import register_mail_settings


def register(context: PluginContext) -> None:
    register_mail_settings()
    context.register_router(router)
