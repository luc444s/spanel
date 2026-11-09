from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "app_deploy", ROOT / "scripts/app_deploy.py"
)
assert SPEC and SPEC.loader
app = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = app
SPEC.loader.exec_module(app)


def project(
    tmp_path: Path, compose: str, dockerfile: str = "FROM neutral:1\nUSER 1001\n"
) -> Path:
    tmp_path.joinpath("compose.yaml").write_text(compose, encoding="utf-8")
    tmp_path.joinpath("Dockerfile").write_text(dockerfile, encoding="utf-8")
    return tmp_path


def basic(extra: str = "", build: str = "build: .") -> str:
    return f"services:\n  worker:\n    {build}\n{extra}"


def codes(result: app.AuditResult) -> set[str]:
    return {finding.code for finding in result.findings}


def test_discovery_zero_one_multiple_and_explicit(tmp_path: Path) -> None:
    with pytest.raises(app.AppDeployError, match="no Compose"):
        app.discover_compose(tmp_path)
    first = tmp_path / "compose.yaml"
    first.write_text("services: {}", encoding="utf-8")
    assert app.discover_compose(tmp_path) == first
    second = tmp_path / "docker-compose.dokploy.yml"
    second.write_text("services: {}", encoding="utf-8")
    with pytest.raises(app.AppDeployError, match="multiple.*compose.yaml.*dokploy"):
        app.discover_compose(tmp_path)
    assert app.discover_compose(tmp_path, second.name) == second


def test_build_context_and_dockerfile_cannot_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.joinpath("Dockerfile").write_text(
        "FROM neutral\nUSER 2\n", encoding="utf-8"
    )
    root = tmp_path / "source"
    root.mkdir()
    project(root, basic(build="build: ../outside"))
    result = app.audit_project(root, non_root=True)
    assert "compose_build_escape" in codes(result)

    root.joinpath("compose.yaml").write_text(basic(), encoding="utf-8")
    root.joinpath("Dockerfile").unlink()
    root.joinpath("Dockerfile").symlink_to(outside / "Dockerfile")
    with pytest.raises(app.AppDeployError, match="escapes"):
        app.audit_project(root, non_root=True)


@pytest.mark.parametrize(
    ("fragment", "code"),
    [
        ("    privileged: true\n", "compose_privileged"),
        ("    cap_add: [NET_ADMIN]\n", "compose_cap_add"),
        ("    devices: [/dev/null]\n", "compose_devices"),
        ("    network_mode: host\n", "compose_host_network_mode"),
        ("    pid: host\n", "compose_host_pid"),
        ("    ipc: host\n", "compose_host_ipc"),
        ("    uts: host\n", "compose_host_uts"),
        ("    userns: host\n", "compose_host_userns"),
        ("    cgroup: host\n", "compose_host_cgroup"),
        ("    security_opt: [seccomp=unconfined]\n", "compose_security_opt_unsafe"),
        ("    user: root\n", "compose_root_user"),
        (
            "    volumes: [/var/run/docker.sock:/run/engine.sock]\n",
            "compose_docker_socket",
        ),
        ("    volumes: [/safe:/data]\n", "compose_bind_absolute"),
        ("    volumes: [../outside:/data]\n", "compose_bind_escape"),
        ("    volumes: [cache:/proc]\n", "compose_mount_sensitive"),
        ("    container_name: global-name\n", "compose_fixed_container_name"),
    ],
)
def test_compose_forbidden_matrix(tmp_path: Path, fragment: str, code: str) -> None:
    result = app.audit_project(project(tmp_path, basic(fragment)))
    assert code in codes(result)
    finding = next(item for item in result.findings if item.code == code)
    assert finding.path == "compose.yaml"
    assert finding.line > 0
    assert finding.remediation


@pytest.mark.parametrize("key", ["network", "entitlements"])
def test_privileged_build_options(tmp_path: Path, key: str) -> None:
    value = "host" if key == "network" else "[network.host]"
    compose = (
        f"services:\n  worker:\n    build:\n      context: .\n      {key}: {value}\n"
    )
    result = app.audit_project(project(tmp_path, compose))
    assert any(item.code.startswith("compose_build_") for item in result.errors)


def test_external_and_fixed_resources_are_rejected(tmp_path: Path) -> None:
    compose = basic("    volumes: [cache:/data]\n") + (
        "volumes:\n  cache:\n    external: true\n"
        "networks:\n  default:\n    name: fixed\n"
    )
    result = app.audit_project(project(tmp_path, compose))
    assert {"compose_external_resource", "compose_fixed_resource_name"} <= codes(result)


def test_versioned_secret_sources_are_rejected_without_echoing_values(
    tmp_path: Path,
) -> None:
    canary = "unique-canary-value"
    compose = f"""services:
  worker:
    build:
      context: .
      args:
        API_TOKEN: {canary}
    env_file: local.env
    environment:
      PRIVATE_KEY: {canary}
secrets:
  item:
    file: secret.txt
"""
    result = app.audit_project(project(tmp_path, compose))
    assert {
        "compose_repo_env_file",
        "compose_inline_secret",
        "compose_inline_build_secret",
        "compose_repo_secret_file",
    } <= codes(result)
    assert canary not in app.render_report(result)


def test_require_build_is_generic(tmp_path: Path) -> None:
    result = app.audit_project(
        project(tmp_path, basic(build="image: neutral:1")), require_build=True
    )
    assert "compose_build_required" in codes(result)
    assert app.audit_project(tmp_path).errors == []


@pytest.mark.parametrize(
    "dockerfile",
    [
        "FROM neutral\n",
        "FROM neutral\nUSER root\n",
        "FROM neutral\nUSER 0:123\n",
        "FROM neutral\nUSER 20\nFROM neutral\n",
        "FROM neutral\nUSER $APP_UID\n",
    ],
)
def test_non_root_requires_final_stage_user(tmp_path: Path, dockerfile: str) -> None:
    result = app.audit_project(project(tmp_path, basic(), dockerfile), non_root=True)
    assert "dockerfile_final_user_root" in codes(result)


def test_dockerfile_findings_have_stable_lines_and_json(tmp_path: Path) -> None:
    dockerfile = """FROM neutral
ADD https://invalid.example/item /tmp/item
ARG API_TOKEN
RUN wget https://invalid.example/x | sh
RUN chmod 777 /work && chmod u+s /work/tool
USER 1001
"""
    result = app.audit_project(project(tmp_path, basic(), dockerfile), non_root=True)
    expected = {
        "dockerfile_remote_add",
        "dockerfile_apparent_secret",
        "dockerfile_pipe_shell",
        "dockerfile_chmod_777",
        "dockerfile_privilege_artifact",
        "dockerfile_no_healthcheck",
    }
    assert expected <= codes(result)
    parsed = json.loads(app.render_report(result, "json"))
    assert parsed["errors"] == 0
    for finding in parsed["findings"]:
        assert set(finding) == {
            "severity",
            "code",
            "service",
            "path",
            "line",
            "message",
            "remediation",
        }
        assert finding["line"] > 0
    assert "API_TOKEN" not in app.render_report(result)


def test_multistage_final_health_and_user_are_used(tmp_path: Path) -> None:
    dockerfile = """FROM neutral AS build
USER root
FROM neutral
USER app
HEALTHCHECK CMD ["check"]
"""
    result = app.audit_project(project(tmp_path, basic(), dockerfile), non_root=True)
    assert not result.errors
    assert "dockerfile_no_healthcheck" not in codes(result)


def test_env_file_must_be_external_private_regular_and_not_symlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    env = tmp_path / "app.env"
    env.write_text("CANARY=top-secret\n", encoding="utf-8")
    env.chmod(0o600)
    assert app.validate_env_file(env, root) == env.resolve()
    env.chmod(0o640)
    with pytest.raises(app.AppDeployError, match="group or others"):
        app.validate_env_file(env, root)
    env.chmod(0o600)
    link = tmp_path / "link.env"
    link.symlink_to(env)
    with pytest.raises(app.AppDeployError, match="non-symlink"):
        app.validate_env_file(link, root)
    internal = root / "internal.env"
    internal.write_text("x=y", encoding="utf-8")
    internal.chmod(0o600)
    with pytest.raises(app.AppDeployError, match="outside"):
        app.validate_env_file(internal, root)


def test_override_adds_confinement_and_ownership() -> None:
    rendered = yaml_load(
        app._override(
            {"services": {"worker": {}}, "volumes": {"data": {}}}, "demo", True
        )
    )
    service = rendered["services"]["worker"]
    assert service["image"].startswith("spanel-app-")
    assert service["image"].endswith(":latest")
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["labels"][app.OWNER_LABEL] == "demo"
    assert rendered["volumes"]["data"]["labels"][app.OWNER_LABEL] == "demo"


def yaml_load(text: str) -> dict[str, object]:
    import yaml

    return yaml.safe_load(text)


class NoCallRunner:
    transport = "local"

    def __init__(self) -> None:
        self.environ: dict[str, str] = {}

    def run(self, *_args: object, **_kwargs: object) -> app.Completed:
        raise AssertionError("dry-run must execute zero commands")


def deploy_args(**updates: object) -> app.DeployOptions:
    values = {
        "repo": None,
        "branch": "main",
        "env_file": None,
        "dry_run": True,
        "project": None,
        "transport": "local",
        "non_root": True,
        "timeout": 20,
        "wait": 2,
    }
    values.update(updates)
    return app.DeployOptions(**values)


def test_dry_run_has_zero_runner_calls_and_zero_state_writes(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    result = app.audit_project(project(root, basic()), non_root=True)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert app.deploy(deploy_args(), result, NoCallRunner()) is None
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert after == before


def test_audit_error_blocks_before_runner(tmp_path: Path) -> None:
    result = app.audit_project(project(tmp_path, basic("    privileged: true\n")))
    with pytest.raises(app.AppDeployError, match="audit has errors"):
        app.deploy(deploy_args(dry_run=False), result, NoCallRunner())


class SpyRunner:
    transport = "local"

    def __init__(self, responses: list[app.Completed], state: Path):
        self.responses = responses
        self.calls: list[list[str]] = []
        self.environ = {"SPANEL_APP_STATE_ROOT": str(state)}

    def run(self, argv: list[str], **_kwargs: object) -> app.Completed:
        self.calls.append(argv)
        return self.responses.pop(0) if self.responses else app.Completed(0)


def test_ownership_collision_fails_before_build(tmp_path: Path) -> None:
    runner = SpyRunner(
        [app.Completed(0, "foreign\n"), app.Completed(0, "other\n")], tmp_path / "state"
    )
    with pytest.raises(app.AppDeployError, match="unowned container"):
        app._ensure_owned(runner, "demo")
    assert not any("build" in call for call in runner.calls)


@pytest.mark.parametrize("value", ["", "root", "0", "0:55", "root:55"])
def test_root_user_detection(value: str) -> None:
    assert app._is_root_user(value)


def test_post_build_root_image_aborts(tmp_path: Path) -> None:
    release = tmp_path / "release"
    runner = SpyRunner(
        [
            app.Completed(
                0,
                json.dumps({"services": {"worker": {"image": "image-id"}}}),
            ),
            app.Completed(0, "0\n"),
        ],
        tmp_path,
    )
    with pytest.raises(app.AppDeployError, match="definite non-root"):
        app._image_users(runner, release, "compose.yaml", "demo", False, ["worker"])
    assert not any("up" in call for call in runner.calls)


@pytest.mark.parametrize(
    "top", ["PID UID\n22 0\n", "PID UID\n22 root\n", "PID UID\n"]
)
def test_effective_root_or_unknown_process_aborts(tmp_path: Path, top: str) -> None:
    runner = SpyRunner(
        [
            app.Completed(0, "running healthy\n"),
            app.Completed(0, "demo\n"),
            app.Completed(0, "1001\n"),
            app.Completed(0, top),
        ],
        tmp_path,
    )
    with pytest.raises(app.AppDeployError, match="effective processes"):
        app._verify_containers(runner, {"worker": "container"}, "demo", True, 1)


def test_clone_uses_gh_single_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[list[str]] = []

    class CloneRunner:
        def run(self, argv: list[str], **_kwargs: object) -> app.Completed:
            seen.append(argv)
            return app.Completed(0)

    app.clone_repo(
        "owner/repository", "feature/topic", tmp_path / "clone", CloneRunner()
    )
    assert seen == [
        [
            "gh",
            "repo",
            "clone",
            "owner/repository",
            str(tmp_path / "clone"),
            "--",
            "--branch",
            "feature/topic",
            "--single-branch",
        ]
    ]


def test_manifest_excludes_env_values(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    result = app.audit_project(project(root, basic()), non_root=True)
    args = deploy_args(repo="owner/repository", env_file=tmp_path / "secret.env")
    manifest = app._manifest_data(
        args,
        result,
        "demo",
        {"worker": "sha256:x"},
        "running",
        "local",
        tmp_path / "release",
    )
    text = json.dumps(manifest)
    assert "top-secret-canary" not in text
    assert set(manifest) >= {
        "commit",
        "tree_hash",
        "compose",
        "services",
        "images",
        "host",
        "status",
    }


def test_log_redaction_removes_all_external_env_values(tmp_path: Path) -> None:
    release = tmp_path / "demo"
    release.mkdir()
    release.joinpath("app.env").write_text(
        "TOKEN=top-secret-canary\nOTHER='second-secret'\n", encoding="utf-8"
    )
    runner = SpyRunner([], tmp_path)
    values = app._redaction_values(runner, release)
    output = app._redact("top-secret-canary / second-secret", values)
    assert output == "[REDACTED] / [REDACTED]"


def test_project_validation_and_purge_confirmation(tmp_path: Path) -> None:
    assert app.main(["status", "--project", "../escape"]) == 2
    manifest_dir = tmp_path / "demo"
    manifest_dir.mkdir()
    manifest = {
        "project": "demo",
        "owner_label": app.OWNER_LABEL,
        "release": str(manifest_dir),
        "compose": "compose.yaml",
        "has_env": False,
    }
    manifest_dir.joinpath(app.MANIFEST).write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    runner = SpyRunner([], tmp_path)
    args = app.LifecycleOptions(
        command="down",
        project="demo",
        transport="local",
        purge_data=True,
        confirm_purge=None,
    )
    monkey = pytest.MonkeyPatch()
    monkey.setattr(app, "_ensure_owned", lambda *_: None)
    try:
        with pytest.raises(app.AppDeployError, match="confirm-purge"):
            app.lifecycle(args, runner)
    finally:
        monkey.undo()


def test_script_has_no_service_specific_runtime_knowledge() -> None:
    source = (ROOT / "scripts/app_deploy.py").read_text(encoding="utf-8").lower()
    forbidden = [
        "postgre" + "sql",
        "my" + "sql",
        "mongo" + "db",
        "elastic" + "search",
        "sql " + "server",
    ]
    assert not any(word in source for word in forbidden)
