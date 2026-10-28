from __future__ import annotations

from plugins.mail.backend.certificates.config import CertificatesConfig
from plugins.mail.backend.certificates.router import router as certificates_router
from plugins.mail.backend.router import router
from plugins.mail.backend.settings import register_mail_settings
from systutor.sdk.context import PluginContext


def register(context: PluginContext) -> None:
    register_mail_settings()
    app = context.router_registry
    if app is not None:
        app.state.mail_context = context
        app.state.mail_certificates_config = CertificatesConfig.from_env()
    context.register_router(router)
    context.register_router(certificates_router)
