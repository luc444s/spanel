from systutor.kernel.plugins.runtime import PluginManifestRegistry


def test_runtime_registry_discovers_existing_manifest(app) -> None:
    registry = PluginManifestRegistry(app.state.settings.plugins_dir)
    registry.discover()

    manifests = registry.list()
    assert any(manifest.id == "alpha" for manifest in manifests)
