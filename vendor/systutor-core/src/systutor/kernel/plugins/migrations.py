from __future__ import annotations

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import cast

from sqlalchemy.orm import Session

from systutor.core.errors import AppError
from systutor.kernel.plugins.models import PluginRegistry
from systutor.kernel.plugins.runtime import DiscoveredPlugin

MigrationFn = Callable[[Session], None]


class PluginMigrationError(AppError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message, status_code=status_code, code="plugin_migration_error")


@dataclass(slots=True)
class PluginMigrationStep:
    revision: str
    module_path: Path
    upgrade: MigrationFn
    downgrade: MigrationFn | None


def list_plugin_migrations(discovered: DiscoveredPlugin) -> list[PluginMigrationStep]:
    migrations_dir = discovered.root / "migrations"
    if not migrations_dir.exists():
        return []

    steps: list[PluginMigrationStep] = []
    for module_path in sorted(migrations_dir.glob("*.py")):
        if module_path.name.startswith("__"):
            continue
        steps.append(_load_migration_step(discovered, module_path))

    revisions = [step.revision for step in steps]
    if len(revisions) != len(set(revisions)):
        raise PluginMigrationError(
            f"duplicate plugin migration revision detected for {discovered.plugin_id}",
        )
    return steps


def get_latest_plugin_migration_version(discovered: DiscoveredPlugin) -> str | None:
    migrations = list_plugin_migrations(discovered)
    if not migrations:
        return None
    return migrations[-1].revision


def upgrade_plugin_migrations(
    db: Session,
    *,
    record: PluginRegistry,
    discovered: DiscoveredPlugin,
    target_revision: str | None = None,
) -> str | None:
    steps = list_plugin_migrations(discovered)
    if not steps:
        return None

    step_index = {step.revision: index for index, step in enumerate(steps)}
    current_index = _resolve_current_index(record.migration_version, step_index)
    target_index = _resolve_target_upgrade_index(target_revision, step_index, steps)

    if target_index <= current_index:
        return record.migration_version

    plan = steps[current_index + 1 : target_index + 1]
    savepoint = db.begin_nested()
    try:
        applied_version = record.migration_version
        for step in plan:
            step.upgrade(db)
            applied_version = step.revision
        savepoint.commit()
        return applied_version
    except Exception:
        savepoint.rollback()
        raise


def downgrade_plugin_migrations(
    db: Session,
    *,
    record: PluginRegistry,
    discovered: DiscoveredPlugin,
    target_revision: str | None = None,
) -> str | None:
    steps = list_plugin_migrations(discovered)
    if not steps or record.migration_version is None:
        return record.migration_version

    step_index = {step.revision: index for index, step in enumerate(steps)}
    current_index = _resolve_current_index(record.migration_version, step_index)
    target_index = _resolve_target_downgrade_index(target_revision, step_index, current_index)

    if target_index == current_index:
        return record.migration_version

    plan = list(reversed(steps[target_index + 1 : current_index + 1]))
    savepoint = db.begin_nested()
    try:
        for step in plan:
            if step.downgrade is None:
                raise PluginMigrationError(
                    f"plugin migration {step.revision} does not define downgrade()",
                    status_code=409,
                )
            step.downgrade(db)
        savepoint.commit()
    except Exception:
        savepoint.rollback()
        raise

    if target_index < 0:
        return None
    return steps[target_index].revision


def rollback_plugin_migrations(
    db: Session,
    *,
    record: PluginRegistry,
    discovered: DiscoveredPlugin,
) -> str | None:
    return downgrade_plugin_migrations(
        db,
        record=record,
        discovered=discovered,
        target_revision=None,
    )


def _load_migration_step(discovered: DiscoveredPlugin, module_path: Path) -> PluginMigrationStep:
    module = _load_migration_module(discovered, module_path)
    revision = getattr(module, "revision", module_path.stem)
    if not isinstance(revision, str) or not revision.strip():
        raise PluginMigrationError(
            f"plugin migration revision is invalid: {module_path}",
        )

    upgrade = getattr(module, "upgrade", None)
    if upgrade is None or not callable(upgrade):
        raise PluginMigrationError(
            f"plugin migration missing upgrade(): {module_path}",
        )

    downgrade = getattr(module, "downgrade", None)
    if downgrade is not None and not callable(downgrade):
        raise PluginMigrationError(
            f"plugin migration downgrade must be callable: {module_path}",
        )

    return PluginMigrationStep(
        revision=revision,
        module_path=module_path,
        upgrade=cast(MigrationFn, upgrade),
        downgrade=cast(MigrationFn | None, downgrade),
    )


def _load_migration_module(discovered: DiscoveredPlugin, module_path: Path) -> ModuleType:
    import_name = f"systutor_plugin_migration_{discovered.plugin_id}_{module_path.stem}"
    spec = importlib.util.spec_from_file_location(import_name, module_path)
    if spec is None or spec.loader is None:
        raise PluginMigrationError(f"cannot load plugin migration: {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_current_index(
    current_version: str | None,
    step_index: dict[str, int],
) -> int:
    if current_version is None:
        return -1
    if current_version not in step_index:
        raise PluginMigrationError(
            f"current plugin migration version not found: {current_version}",
            status_code=409,
        )
    return step_index[current_version]


def _resolve_target_upgrade_index(
    target_revision: str | None,
    step_index: dict[str, int],
    steps: list[PluginMigrationStep],
) -> int:
    if target_revision is None:
        return len(steps) - 1
    if target_revision not in step_index:
        raise PluginMigrationError(
            f"target plugin migration version not found: {target_revision}",
        )
    return step_index[target_revision]


def _resolve_target_downgrade_index(
    target_revision: str | None,
    step_index: dict[str, int],
    current_index: int,
) -> int:
    if target_revision is None:
        return current_index - 1
    if target_revision not in step_index:
        raise PluginMigrationError(
            f"target plugin migration version not found: {target_revision}",
        )
    return step_index[target_revision]
