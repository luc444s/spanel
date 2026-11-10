"""Framework-independent Compose application runtime."""

from .audit import audit_project
from .deployment import (
    _ensure_owned,
    _image_users,
    _manifest_data,
    _override,
    _service_environment_names,
    _validate_compose_environment,
    _verify_containers,
    deploy,
    render_report,
    runtime_snapshot,
    validate_env_file,
)
from .docker_audit import _is_root_user
from .lifecycle import _redact, _redaction_values, lifecycle
from .source import clone_repo, compose_candidates, discover_compose
from .transport import Runner
from .types import (
    MANIFEST,
    OWNER_LABEL,
    AppDeployError,
    AuditResult,
    Completed,
    DeployOptions,
    Finding,
    LifecycleOptions,
    LifecycleResult,
)

__all__ = [
    "AppDeployError",
    "AuditResult",
    "Completed",
    "DeployOptions",
    "Finding",
    "LifecycleOptions",
    "LifecycleResult",
    "MANIFEST",
    "OWNER_LABEL",
    "Runner",
    "_ensure_owned",
    "_image_users",
    "_is_root_user",
    "_manifest_data",
    "_override",
    "_service_environment_names",
    "_validate_compose_environment",
    "_redact",
    "_redaction_values",
    "_verify_containers",
    "audit_project",
    "clone_repo",
    "compose_candidates",
    "deploy",
    "discover_compose",
    "lifecycle",
    "render_report",
    "runtime_snapshot",
    "validate_env_file",
]
