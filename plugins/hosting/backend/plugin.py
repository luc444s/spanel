import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.router import router  # noqa: E402
from systutor.sdk import PluginContext  # noqa: E402


def register(context: PluginContext) -> None:
    context.register_router(router)
    context.register_permissions(["hosting.containers.read", "hosting.containers.manage"])
