from pathlib import Path

from systutor.kernel.plugins.runtime import PluginManifestRegistry, load_plugin_backend
from systutor.sdk import PluginContext

PLUGINS_ROOT = Path(__file__).resolve().parents[1] / "plugins"


def test_runtime_registry_discovers_existing_manifest(app) -> None:
    registry = PluginManifestRegistry(app.state.settings.plugins_dir)
    registry.discover()

    manifests = registry.list()
    assert any(manifest.id == "alpha" for manifest in manifests)


def test_bundled_plugins_have_valid_structure() -> None:
    registry = PluginManifestRegistry(PLUGINS_ROOT)
    registry.discover()

    errors = {
        plugin.plugin_id: plugin.error_message
        for plugin in registry.discovered()
        if not plugin.is_valid
    }
    assert errors == {}


def test_apps_backend_entrypoint_loads() -> None:
    registry = PluginManifestRegistry(PLUGINS_ROOT)
    registry.discover()
    plugin = registry.get("apps")

    assert plugin is not None
    binding = load_plugin_backend(plugin, context_builder=PluginContext)

    assert binding.registration.plugin_id == "apps"
    assert binding.registration.routers
