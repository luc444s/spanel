"""Compose application static audit."""
from __future__ import annotations

from pathlib import Path

from .docker_audit import (
    _audit_dockerfile,
    _audit_environment,
    _audit_volume,
    _finding,
    _is_root_user,
)
from .source import (
    _line,
    _load_compose,
    _resolve_inside,
    discover_compose,
    validate_checkout,
)
from .types import SECRET_NAME_RE, AppDeployError, AuditResult, Finding


def audit_project(
    root: Path,
    compose_file: str | None = None,
    *,
    non_root: bool = False,
    require_build: bool = False,
) -> AuditResult:
    root = validate_checkout(root)
    compose = discover_compose(root, compose_file)
    data, lines = _load_compose(compose)
    compose_rel = compose.relative_to(root).as_posix()
    findings: list[Finding] = []
    dockerfiles: dict[str, Path] = {}
    services = data["services"]
    for service, config in services.items():
        if not isinstance(service, str) or not isinstance(config, dict):
            raise AppDeployError(
                "Compose service names/configurations must be mappings"
            )
        base = ("services", service)
        for key, code, message, remediation in (
            (
                "privileged",
                "compose_privileged",
                "Privileged containers are forbidden.",
                "Remove privileged and grant no broad host access.",
            ),
            (
                "cap_add",
                "compose_cap_add",
                "Added Linux capabilities are forbidden.",
                "Remove cap_add and redesign around default privileges.",
            ),
            (
                "devices",
                "compose_devices",
                "Host device access is forbidden.",
                "Remove devices from the service.",
            ),
        ):
            if key in config and (key != "privileged" or config[key] is True):
                _finding(
                    findings,
                    "error",
                    code,
                    service,
                    compose_rel,
                    _line(lines, *base, key),
                    message,
                    remediation,
                )
        for key in ("network_mode", "pid", "ipc", "uts", "userns", "cgroup"):
            if str(config.get(key, "")).strip().lower() == "host":
                _finding(
                    findings,
                    "error",
                    f"compose_host_{key}",
                    service,
                    compose_rel,
                    _line(lines, *base, key),
                    f"Host {key} namespace is forbidden.",
                    f"Remove {key}: host.",
                )
        if "container_name" in config:
            _finding(
                findings,
                "error",
                "compose_fixed_container_name",
                service,
                compose_rel,
                _line(lines, *base, "container_name"),
                "Fixed container names can collide with resources outside the project.",
                "Remove container_name and let Compose scope the name.",
            )
        if config.get("ports"):
            _finding(
                findings,
                "error",
                "compose_published_port",
                service,
                compose_rel,
                _line(lines, *base, "ports"),
                "Published host ports are outside this laboratory phase.",
                "Remove ports and use only the project-internal network.",
            )
        if "user" in config and _is_root_user(config["user"]):
            _finding(
                findings,
                "error",
                "compose_root_user",
                service,
                compose_rel,
                _line(lines, *base, "user"),
                "Service explicitly selects root user.",
                "Set a non-root user or inherit the image USER.",
            )
        options = config.get("security_opt", [])
        if isinstance(options, str):
            options = [options]
        if not isinstance(options, list):
            options = [str(options)]
        for index, option in enumerate(options):
            normalized = str(option).lower().replace(" ", "")
            if "unconfined" in normalized or normalized in {
                "no-new-privileges:false",
                "no-new-privileges= false",
                "no-new-privileges=false",
            }:
                _finding(
                    findings,
                    "error",
                    "compose_security_opt_unsafe",
                    service,
                    compose_rel,
                    _line(lines, *base, "security_opt", str(index)),
                    "security_opt disables a confinement control.",
                    "Remove the unsafe security option.",
                )
        volumes = config.get("volumes", [])
        if not isinstance(volumes, list):
            volumes = [volumes]
        for index, volume in enumerate(volumes):
            _audit_volume(
                volume,
                root,
                compose.parent,
                compose_rel,
                service,
                _line(lines, *base, "volumes", str(index)),
                findings,
            )
        if "env_file" in config:
            _finding(
                findings,
                "error",
                "compose_repo_env_file",
                service,
                compose_rel,
                _line(lines, *base, "env_file"),
                "Service env_file could load secrets from the checkout.",
                "Remove env_file and pass one external private file to the runner.",
            )
        _audit_environment(
            config.get("environment"),
            compose_rel,
            service,
            _line(lines, *base, "environment"),
            findings,
        )
        build = config.get("build")
        if build is None:
            if require_build:
                _finding(
                    findings,
                    "error",
                    "compose_build_required",
                    service,
                    compose_rel,
                    _line(lines, *base),
                    "Service does not define build.",
                    "Define a checkout-contained build context and Dockerfile.",
                )
            continue
        if isinstance(build, str):
            context_text, dockerfile_text = build, "Dockerfile"
        elif isinstance(build, dict):
            context_text = str(build.get("context", "."))
            dockerfile_text = str(build.get("dockerfile", "Dockerfile"))
            if str(build.get("network", "")).lower() == "host":
                _finding(
                    findings,
                    "error",
                    "compose_build_host_network",
                    service,
                    compose_rel,
                    _line(lines, *base, "build", "network"),
                    "Build uses the host network.",
                    "Remove build.network: host.",
                )
            if build.get("entitlements"):
                _finding(
                    findings,
                    "error",
                    "compose_build_entitlements",
                    service,
                    compose_rel,
                    _line(lines, *base, "build", "entitlements"),
                    "Build entitlements are forbidden.",
                    "Remove privileged build entitlements.",
                )
            build_args = build.get("args", {})
            if isinstance(build_args, dict):
                for name, value in build_args.items():
                    text = "" if value is None else str(value).strip()
                    if (
                        SECRET_NAME_RE.search(str(name))
                        and text
                        and not text.startswith("${")
                    ):
                        _finding(
                            findings,
                            "error",
                            "compose_inline_build_secret",
                            service,
                            compose_rel,
                            _line(lines, *base, "build", "args", str(name)),
                            "A secret-like build argument has a literal versioned value.",
                            "Remove it from build args and inject secrets only at runtime.",
                        )
        else:
            _finding(
                findings,
                "error",
                "compose_build_invalid",
                service,
                compose_rel,
                _line(lines, *base, "build"),
                "Build configuration is invalid.",
                "Use a context string or mapping.",
            )
            continue
        try:
            context = _resolve_inside(
                compose.parent / context_text, root, f"build context for {service}"
            )
            dockerfile = _resolve_inside(
                context / dockerfile_text, root, f"Dockerfile for {service}"
            )
            if not context.is_dir() or not dockerfile.is_file():
                raise AppDeployError("build context/Dockerfile has wrong type")
        except AppDeployError:
            _finding(
                findings,
                "error",
                "compose_build_escape",
                service,
                compose_rel,
                _line(lines, *base, "build"),
                "Build context or Dockerfile is missing or escapes checkout.",
                "Keep both inside the checkout and avoid escaping symlinks.",
            )
            continue
        dockerfiles[service] = dockerfile
        _audit_dockerfile(dockerfile, root, service, non_root, findings)
    for section in ("volumes", "networks"):
        entries = data.get(section, {}) or {}
        if not isinstance(entries, dict):
            raise AppDeployError(f"Compose {section} must be a mapping")
        for name, config in entries.items():
            if isinstance(config, dict) and config.get("external"):
                _finding(
                    findings,
                    "error",
                    "compose_external_resource",
                    None,
                    compose_rel,
                    _line(lines, section, str(name)),
                    "External resources have uncertain ownership.",
                    "Use a project-owned non-external resource.",
                )
            if isinstance(config, dict) and config.get("name"):
                _finding(
                    findings,
                    "error",
                    "compose_fixed_resource_name",
                    None,
                    compose_rel,
                    _line(lines, section, str(name), "name"),
                    "Fixed resource name can collide outside the project.",
                    "Remove name and let Compose scope the resource.",
                )
    secrets = data.get("secrets", {}) or {}
    if not isinstance(secrets, dict):
        raise AppDeployError("Compose secrets must be a mapping")
    for name, config in secrets.items():
        _finding(
            findings,
            "error",
            "compose_secret_unsupported",
            None,
            compose_rel,
            _line(lines, "secrets", str(name)),
            "Compose-managed secrets are not owned or staged by this primitive runner.",
            "Use the external private env file for this phase.",
        )
        if isinstance(config, dict) and "file" in config:
            _finding(
                findings,
                "error",
                "compose_repo_secret_file",
                None,
                compose_rel,
                _line(lines, "secrets", str(name), "file"),
                "Compose secret file would be loaded from the checkout.",
                "Use the external private env file supported by this primitive runner.",
            )
    findings.sort(
        key=lambda item: (item.path, item.line, item.service or "", item.code)
    )
    return AuditResult(root, compose, data, dockerfiles, findings)
