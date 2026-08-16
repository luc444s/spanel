import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from systutor.sdk import PluginContext  # noqa: E402


def register(context: PluginContext) -> None:
    context.register_permissions(["hosting.containers.read", "hosting.containers.manage"])
    context.register_events(["hosting.site.adopted"])
