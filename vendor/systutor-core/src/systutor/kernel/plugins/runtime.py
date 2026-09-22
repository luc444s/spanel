from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import cast

from systutor.core.errors import AppError
from systutor.kernel.plugins.manifest import PluginManifest
from systutor.sdk import PluginContext, PluginRegistration

SUPPORTED_PLUGIN_API_VERSION = "1"
REQUIRED_PLUGIN_DIRECTORIES = ("backend", "frontend", "migrations", "permissions", "events")
REQUIRED_PLUGIN_FILES = ("README.md",)


class PluginRuntimeError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=500, code="plugin_runtime_error")


@dataclass(slots=True)
class DiscoveredPlugin:
    plugin_id: str
    root: Path
    manifest: PluginManifest | None = None
    error_message: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.manifest is not None and self.error_message is None


@dataclass(slots=True)
class LoadedPlugin:
    plugin_id: str
    root: Path
    status: str
    manifest: PluginManifest | None = None
    registration: PluginRegistration | None = None
    error_message: str | None = None
    lifecycle: tuple[str, ...] = ()


@dataclass(slots=True)
class PluginBackendBinding:
    manifest: PluginManifest
    module: ModuleType
    context: PluginContext
    registration: PluginRegistration


class PluginManifestRegistry:
    def __init__(self, plugins_dir: Path) -> None:
        self.plugins_dir = plugins_dir
        self._plugins: list[DiscoveredPlugin] = []
        self._plugins_by_id: dict[str, DiscoveredPlugin] = {}

    def discover(self) -> None:
        self._plugins.clear()
        self._plugins_by_id.clear()

        if not self.plugins_dir.exists():
            return

        discovered_plugins: list[DiscoveredPlugin] = []
        for manifest_path in sorted(self.plugins_dir.glob("*/plugin.json")):
            discovered_plugins.append(self._discover_plugin(manifest_path))

        duplicate_ids = self._find_duplicate_plugin_ids(discovered_plugins)
        for plugin in discovered_plugins:
            if plugin.manifest is not None and plugin.manifest.id in duplicate_ids:
                plugin.error_message = f"duplicate plugin id: {plugin.manifest.id}"

        self._plugins = sorted(
            discovered_plugins,
            key=lambda item: (item.plugin_id, str(item.root)),
        )
        valid_plugins = [
            (plugin.manifest, plugin)
            for plugin in self._plugins
            if plugin.manifest is not None and plugin.error_message is None
        ]
        self._plugins_by_id = {manifest.id: plugin for manifest, plugin in valid_plugins}

    def list(self) -> list[PluginManifest]:
        return [
            plugin.manifest
            for plugin in self.discovered()
            if plugin.manifest is not None and plugin.error_message is None
        ]

    def discovered(self) -> list[DiscoveredPlugin]:
        return list(self._plugins)

    def get(self, plugin_id: str) -> DiscoveredPlugin | None:
        return self._plugins_by_id.get(plugin_id)

    @staticmethod
    def _discover_plugin(manifest_path: Path) -> DiscoveredPlugin:
        root = manifest_path.parent
        plugin_id = root.name
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return DiscoveredPlugin(
                plugin_id=plugin_id,
                root=root,
                error_message=str(exc),
            )

        raw_plugin_id = data.get("id")
        if isinstance(raw_plugin_id, str) and raw_plugin_id.strip():
            plugin_id = raw_plugin_id

        try:
            manifest = PluginManifest.model_validate(data)
        except Exception as exc:
            return DiscoveredPlugin(
                plugin_id=plugin_id,
                root=root,
                error_message=str(exc),
            )

        return DiscoveredPlugin(
            plugin_id=plugin_id,
            root=root,
            manifest=manifest,
            error_message=PluginManifestRegistry._structure_error(root, manifest),
        )

    @staticmethod
    def _find_duplicate_plugin_ids(plugins: list[DiscoveredPlugin]) -> set[str]:
        counts: dict[str, int] = {}
        for plugin in plugins:
            if plugin.manifest is None:
                continue
            counts[plugin.manifest.id] = counts.get(plugin.manifest.id, 0) + 1
        return {plugin_id for plugin_id, count in counts.items() if count > 1}

    @staticmethod
    def _structure_error(root: Path, manifest: PluginManifest) -> str | None:
        missing_items = [
            name for name in REQUIRED_PLUGIN_DIRECTORIES if not root.joinpath(name).is_dir()
        ]
        missing_items.extend(
            name for name in REQUIRED_PLUGIN_FILES if not root.joinpath(name).is_file()
        )
        if missing_items:
            return f"missing plugin structure: {', '.join(sorted(missing_items))}"

        module_name, _function_name = manifest.backend_entrypoint.split(":", 1)
        backend_module_path = root.joinpath(*module_name.split(".")).with_suffix(".py")
        if not backend_module_path.is_file():
            return f"backend entrypoint file not found: {backend_module_path}"

        frontend_entrypoint_path = root / manifest.frontend_entrypoint
        if not frontend_entrypoint_path.is_file():
            return f"frontend entrypoint file not found: {frontend_entrypoint_path}"

        return None


class PluginRuntime:
    def __init__(
        self,
        registry: PluginManifestRegistry,
        *,
        context_builder: Callable[[PluginManifest], PluginContext] | None = None,
        supported_api_version: str = SUPPORTED_PLUGIN_API_VERSION,
    ) -> None:
        self.registry = registry
        self._results: dict[str, LoadedPlugin] = {}
        self.context_builder = context_builder or PluginContext
        self.supported_api_version = supported_api_version

    def load(self, *, disabled_plugins: set[str] | None = None) -> None:
        disabled_plugins = disabled_plugins or set()
        ordered_plugins, cyclic_plugins = self._resolve_load_order()
        self._results.clear()

        for discovered in self.registry.discovered():
            if discovered.is_valid:
                continue
            self._results[discovered.plugin_id] = LoadedPlugin(
                plugin_id=discovered.plugin_id,
                root=discovered.root,
                status="failed",
                manifest=discovered.manifest,
                error_message=discovered.error_message,
                lifecycle=("discovered", "failed"),
            )

        for discovered in ordered_plugins:
            manifest = discovered.manifest
            if manifest is None:
                continue
            if manifest.id in cyclic_plugins:
                self._results[manifest.id] = LoadedPlugin(
                    plugin_id=manifest.id,
                    root=discovered.root,
                    status="failed",
                    manifest=manifest,
                    error_message="circular dependency detected",
                    lifecycle=("discovered", "validated", "failed"),
                )
                continue

            api_version_error = self._api_version_error(manifest)
            if api_version_error is not None:
                self._results[manifest.id] = LoadedPlugin(
                    plugin_id=manifest.id,
                    root=discovered.root,
                    status="failed",
                    manifest=manifest,
                    error_message=api_version_error,
                    lifecycle=("discovered", "validated", "failed"),
                )
                continue

            if manifest.id in disabled_plugins:
                self._results[manifest.id] = LoadedPlugin(
                    plugin_id=manifest.id,
                    root=discovered.root,
                    status="disabled",
                    manifest=manifest,
                    lifecycle=("discovered", "validated", "installed", "disabled"),
                )
                continue

            dependency_error = self._dependency_error(manifest)
            if dependency_error is not None:
                self._results[manifest.id] = LoadedPlugin(
                    plugin_id=manifest.id,
                    root=discovered.root,
                    status="failed",
                    manifest=manifest,
                    error_message=dependency_error,
                    lifecycle=("discovered", "validated", "failed"),
                )
                continue

            try:
                registration = self._load_registration(discovered)
                self._results[manifest.id] = LoadedPlugin(
                    plugin_id=manifest.id,
                    root=discovered.root,
                    status="enabled",
                    manifest=manifest,
                    registration=registration,
                    lifecycle=("discovered", "validated", "installed", "enabled"),
                )
            except Exception as exc:
                self._results[manifest.id] = LoadedPlugin(
                    plugin_id=manifest.id,
                    root=discovered.root,
                    status="failed",
                    manifest=manifest,
                    error_message=str(exc),
                    lifecycle=("discovered", "validated", "failed"),
                )

    def list_results(self) -> list[LoadedPlugin]:
        if self._results:
            return [self._results[key] for key in sorted(self._results)]
        return [
            LoadedPlugin(
                plugin_id=plugin.plugin_id,
                root=plugin.root,
                status="discovered" if plugin.is_valid else "failed",
                manifest=plugin.manifest,
                error_message=plugin.error_message,
                lifecycle=("discovered",) if plugin.is_valid else ("discovered", "failed"),
            )
            for plugin in self.registry.discovered()
        ]

    def collect_event_handlers(self) -> dict[str, dict[str, list]]:
        handlers: dict[str, dict[str, list]] = {}
        for result in self.list_results():
            if result.status != "enabled" or result.registration is None:
                continue
            handlers[result.plugin_id] = result.registration.event_handlers
        return handlers

    def _dependency_error(self, manifest: PluginManifest) -> str | None:
        for dependency_id in manifest.requires:
            dependency = self._results.get(dependency_id)
            if dependency is None:
                return f"missing dependency: {dependency_id}"
            if dependency.status != "enabled":
                return f"dependency not enabled: {dependency_id}"
        return None

    def _resolve_load_order(self) -> tuple[list[DiscoveredPlugin], set[str]]:
        valid_plugins = [
            (plugin.manifest, plugin)
            for plugin in self.registry.discovered()
            if plugin.manifest is not None and plugin.error_message is None
        ]
        discovered = {manifest.id: plugin for manifest, plugin in valid_plugins}
        indegree = {plugin_id: 0 for plugin_id in discovered}
        dependents: dict[str, list[str]] = {plugin_id: [] for plugin_id in discovered}

        for plugin_id, plugin in discovered.items():
            manifest = plugin.manifest
            if manifest is None:
                continue
            for dependency_id in manifest.requires:
                if dependency_id in discovered:
                    indegree[plugin_id] += 1
                    dependents[dependency_id].append(plugin_id)

        queue = sorted([plugin_id for plugin_id, degree in indegree.items() if degree == 0])
        ordered_ids: list[str] = []

        while queue:
            plugin_id = queue.pop(0)
            ordered_ids.append(plugin_id)
            for dependent_id in sorted(dependents[plugin_id]):
                indegree[dependent_id] -= 1
                if indegree[dependent_id] == 0:
                    queue.append(dependent_id)
                    queue.sort()

        cyclic_plugins = {plugin_id for plugin_id, degree in indegree.items() if degree > 0}
        remaining = sorted(cyclic_plugins)
        ordered_ids.extend(remaining)
        return [discovered[plugin_id] for plugin_id in ordered_ids], cyclic_plugins

    def _load_registration(self, discovered: DiscoveredPlugin) -> PluginRegistration:
        return load_plugin_backend(discovered, context_builder=self.context_builder).registration

    def _api_version_error(self, manifest: PluginManifest) -> str | None:
        plugin_major = manifest.api_version.split(".", 1)[0]
        runtime_major = self.supported_api_version.split(".", 1)[0]
        if plugin_major != runtime_major:
            return (
                f"incompatible api_version: plugin={manifest.api_version} "
                f"runtime={self.supported_api_version}"
            )
        return None

    @staticmethod
    def _validate_registration(
        manifest: PluginManifest,
        registration: PluginRegistration,
    ) -> None:
        if registration.plugin_id != manifest.id:
            raise PluginRuntimeError(
                "plugin registration id mismatch: "
                f"expected {manifest.id}, got {registration.plugin_id}"
            )

        undeclared_permissions = [
            permission
            for permission in registration.permissions
            if permission not in manifest.permissions
        ]
        if undeclared_permissions:
            raise PluginRuntimeError(
                f"undeclared plugin permissions: {', '.join(sorted(undeclared_permissions))}"
            )

        undeclared_events = [
            event_name for event_name in registration.events if event_name not in manifest.events
        ]
        if undeclared_events:
            raise PluginRuntimeError(
                f"undeclared plugin events: {', '.join(sorted(undeclared_events))}"
            )

        invalid_handler_events = [
            event_name
            for event_name in registration.event_handlers
            if not isinstance(event_name, str) or not event_name.strip() or "." not in event_name
        ]
        if invalid_handler_events:
            raise PluginRuntimeError(
                "event handlers must target fully-qualified events: "
                f"{', '.join(sorted(invalid_handler_events))}"
            )

    @staticmethod
    def _load_module(discovered: DiscoveredPlugin, module_name: str) -> ModuleType:
        plugin_id = discovered.plugin_id
        module_path = discovered.root.joinpath(*module_name.split(".")).with_suffix(".py")
        if not module_path.exists():
            raise PluginRuntimeError(f"plugin module not found: {module_path}")

        import_name = f"systutor_plugin_{plugin_id}_{module_name.replace('.', '_')}"
        spec = importlib.util.spec_from_file_location(import_name, module_path)
        if spec is None or spec.loader is None:
            raise PluginRuntimeError(f"cannot load plugin module: {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def load_plugin_backend(
    discovered: DiscoveredPlugin,
    *,
    context_builder: Callable[[PluginManifest], PluginContext],
) -> PluginBackendBinding:
    manifest = discovered.manifest
    if manifest is None:
        raise PluginRuntimeError(f"plugin manifest not available: {discovered.plugin_id}")

    module_name, function_name = manifest.backend_entrypoint.split(":", 1)
    module = PluginRuntime._load_module(discovered, module_name)
    register = getattr(module, function_name, None)
    if register is None or not callable(register):
        raise PluginRuntimeError(
            f"entrypoint invalido para plugin {manifest.id}: {manifest.backend_entrypoint}"
        )

    context = context_builder(manifest)
    returned_registration = register(context)
    registration = context.registration
    if returned_registration is not None:
        if not isinstance(returned_registration, PluginRegistration):
            raise PluginRuntimeError(
                f"plugin register must return PluginRegistration or None: {manifest.id}"
            )
        registration = returned_registration

    if not registration.permissions:
        registration.permissions.extend(list(manifest.permissions))
    if not registration.events:
        registration.events.extend(list(manifest.events))

    PluginRuntime._validate_registration(manifest, registration)
    return PluginBackendBinding(
        manifest=manifest,
        module=module,
        context=context,
        registration=registration,
    )


def get_plugin_lifecycle_hook(
    binding: PluginBackendBinding,
    hook_name: str,
) -> Callable[[PluginContext], None] | None:
    hook = getattr(binding.module, hook_name, None)
    if hook is None:
        return None
    if not callable(hook):
        raise PluginRuntimeError(
            f"plugin lifecycle hook must be callable: {binding.manifest.id}.{hook_name}"
        )
    return cast(Callable[[PluginContext], None], hook)
