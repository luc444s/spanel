from plugins.filemanager.backend.router import router
from systutor.sdk import PluginContext


def register(context: PluginContext) -> None:
    context.register_router(router)
