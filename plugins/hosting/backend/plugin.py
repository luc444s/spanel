import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter

from systutor.sdk import PluginContext

BACKEND_ROOT = Path(__file__).resolve().parent


def _load_local_module(module_filename: str, import_name: str):
    if import_name in sys.modules:
        return sys.modules[import_name]
    module_path = BACKEND_ROOT / module_filename
    spec = importlib.util.spec_from_file_location(import_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"no se pudo cargar {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[import_name] = module
    spec.loader.exec_module(module)
    return module


shared = _load_local_module("shared.py", "spanel_hosting_shared")
routes_sites = _load_local_module("routes_sites.py", "spanel_hosting_routes_sites")
routes_backups = _load_local_module("routes_backups.py", "spanel_hosting_routes_backups")
routes_access = _load_local_module("routes_access.py", "spanel_hosting_routes_access")
routes_runtime = _load_local_module("routes_runtime.py", "spanel_hosting_routes_runtime")

router = APIRouter(tags=["hosting"])
router.include_router(routes_sites.router)
router.include_router(routes_backups.router)
router.include_router(routes_access.router)
router.include_router(routes_runtime.router)


def register(context: PluginContext) -> None:
    context.register_router(router)
    context.register_permissions(
        [
            "hosting.sites.read",
            "hosting.sites.adopt",
            "hosting.sites.provision",
            "hosting.sites.update",
            "hosting.sites.delete",
            "hosting.runtime.read",
            "hosting.runtime.manage",
            "hosting.access.read",
            "hosting.backups.read",
            "hosting.backups.create",
            "hosting.files.manage",
            "hosting.sso.create",
        ]
    )
    context.register_events(["hosting.site.adopted"])
