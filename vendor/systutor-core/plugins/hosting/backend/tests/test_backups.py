from __future__ import annotations

import io
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugins.hosting.backend import backup_tickets
from plugins.hosting.backend.backups import BackupService
from plugins.hosting.backend.settings import HostingSettings
from systutor.core.errors import AppError


def _settings(tmp_path: Path, **overrides) -> HostingSettings:
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True)
    for name in ("site_backup.py", "site_backup_link.py", "site_restore.py"):
        (scripts / name).write_text("# fixture\n", encoding="utf-8")
    values = {
        "docker_transport": "local",
        "backups_dir": str(tmp_path / "private"),
        "backup_scripts_dir": str(scripts),
        "backup_link_ttl": 300,
        "backup_max_upload_bytes": 1024 * 1024,
    }
    values.update(overrides)
    return HostingSettings(**values)


def _site(container: str = "trusted-container") -> SimpleNamespace:
    return SimpleNamespace(id="site-123", container_name=container)


def _running(*_args, **_kwargs) -> dict:
    return {"State": {"Status": "running", "Running": True}}


def _result(argv: list[str], code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, code, "private stdout", "private stderr")


def test_export_runs_only_the_validated_script_with_server_owned_values(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[list[str]] = []
    ticket_payload: dict[str, str] = {}

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        output = Path(argv[argv.index("--out") + 1])
        (output / "wp-trusted-container.zip").write_bytes(b"zip")
        return _result(argv)

    monkeypatch.setattr(backup_tickets, "require_store", lambda: None)

    def issue(payload: dict[str, str], ttl: int) -> str:
        ticket_payload.update(payload)
        assert ttl == 300
        return "opaque-ticket"

    monkeypatch.setattr(backup_tickets, "issue", issue)
    service = BackupService(_settings(tmp_path), runner=runner, inspector=_running)
    result = service.export(_site(), user_id="user-1", tenant_id="tenant-1")

    assert result.ticket == "opaque-ticket"
    assert result.filename == "wp-trusted-container.zip"
    assert len(calls) == 1
    assert calls[0][2:4] == ["--container", "trusted-container"]
    assert calls[0][4] == "--out"
    assert ticket_payload == {
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "site_id": "site-123",
        "backup_id": calls[0][5].split("/")[-1],
        "filename": "wp-trusted-container.zip",
    }
    assert "private" not in " ".join(ticket_payload.values())
    assert (Path(calls[0][5]) / result.filename).stat().st_mode & 0o777 == 0o600


def test_wordpress_export_passes_the_resolved_database_to_the_script(
    tmp_path: Path, monkeypatch
) -> None:
    from plugins.hosting.backend import backups as backups_module

    monkeypatch.setattr(backup_tickets, "require_store", lambda: None)
    monkeypatch.setattr(backup_tickets, "issue", lambda payload, ttl: "ticket")
    monkeypatch.setattr(
        backups_module,
        "resolve_database_binding",
        lambda *args, **kwargs: ("db:3306", "real-db", "wpdb", "wpuser", "s3cret"),
    )

    seen: dict[str, object] = {}

    def runner(argv, env=None):
        seen["argv"] = argv
        seen["env"] = env
        output = Path(argv[argv.index("--out") + 1])
        (output / "wp-site.zip").write_bytes(b"zip")
        return _result(argv)

    site = SimpleNamespace(id="site-123", container_name="wp-site", kind="wordpress")
    service = BackupService(_settings(tmp_path), runner=runner, inspector=_running)
    service.export(site, user_id="user-1", tenant_id="tenant-1")

    argv = seen["argv"]
    env = seen["env"]
    assert isinstance(argv, list) and isinstance(env, dict)
    assert argv[argv.index("--kind") + 1] == "wp"
    assert argv[argv.index("--path") + 1] == "/var/www/html"
    assert argv[argv.index("--db-container") + 1] == "real-db"
    assert env["SPANEL_BACKUP_DB_PASSWORD"] == "s3cret"
    assert "s3cret" not in " ".join(argv)


@pytest.mark.parametrize("contents", [(), ("a.zip", "b.zip"), ("not-a-zip.txt",)])
def test_export_rejects_any_surface_other_than_exactly_one_regular_zip(
    tmp_path: Path, monkeypatch, contents: tuple[str, ...]
) -> None:
    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        output = Path(argv[argv.index("--out") + 1])
        for name in contents:
            (output / name).write_bytes(b"fixture")
        return _result(argv)

    monkeypatch.setattr(backup_tickets, "require_store", lambda: None)
    service = BackupService(_settings(tmp_path), runner=runner, inspector=_running)
    with pytest.raises(AppError) as error:
        service.export(_site(), user_id="user", tenant_id="tenant")
    assert error.value.code == "hosting_backup_runtime_unavailable"
    assert list(service.exports.iterdir()) == []


def test_transport_confirmation_and_lock_guards_do_not_read_upload_or_run(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    class Unreadable(io.BytesIO):
        def read(self, *_args, **_kwargs):
            raise AssertionError("upload was read")

    source = Unreadable(b"zip")
    ssh = BackupService(
        _settings(tmp_path, docker_transport="ssh"),
        runner=lambda argv: calls.append(argv) or _result(argv),
        inspector=_running,
    )
    with pytest.raises(AppError) as transport:
        ssh.restore(_site(), filename="backup.zip", confirmation="trusted-container", source=source)
    assert transport.value.code == "hosting_backup_transport_unsupported"

    local = BackupService(
        _settings(tmp_path / "local"),
        runner=lambda argv: calls.append(argv) or _result(argv),
        inspector=_running,
    )
    with pytest.raises(AppError) as confirmation:
        local.restore(_site(), filename="backup.zip", confirmation="wrong", source=source)
    assert confirmation.value.code == "hosting_backup_confirmation_mismatch"

    local.initialize()
    with local._site_lock("site-123"):
        with pytest.raises(AppError) as busy:
            local.restore(
                _site(),
                filename="backup.zip",
                confirmation="trusted-container",
                source=source,
            )
    assert busy.value.code == "hosting_backup_busy"
    assert calls == []


def test_upload_is_streamed_with_a_hard_limit_and_removed_on_rejection(tmp_path: Path) -> None:
    service = BackupService(
        _settings(tmp_path, backup_max_upload_bytes=4),
        runner=lambda argv: pytest.fail(f"runner called: {argv}"),
        inspector=_running,
    )
    with pytest.raises(AppError) as error:
        service.restore(
            _site(),
            filename="backup.zip",
            confirmation="trusted-container",
            source=io.BytesIO(b"12345"),
        )
    assert error.value.status_code == 413
    assert error.value.code == "hosting_backup_upload_too_large"
    assert list(service.imports.iterdir()) == []


@pytest.mark.parametrize(
    ("exit_code", "status", "code"),
    [
        (1, 409, "hosting_backup_restore_failed"),
        (2, 422, "hosting_backup_invalid"),
        (3, 500, "hosting_backup_rollback_failed"),
        (17, 503, "hosting_backup_runtime_unavailable"),
    ],
)
def test_restore_exit_mapping_cleanup_and_opaque_rollback_reference(
    tmp_path: Path, exit_code: int, status: int, code: str
) -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        safety = Path(argv[argv.index("--safety-out") + 1]) / "safety-created-by-script"
        safety.mkdir()
        (safety / "backup.zip").write_bytes(b"safety")
        return _result(argv, exit_code)

    service = BackupService(_settings(tmp_path), runner=runner, inspector=_running)
    with pytest.raises(AppError) as error:
        service.restore(
            _site(),
            filename="hostile-name.zip",
            confirmation="trusted-container",
            source=io.BytesIO(b"incoming"),
        )
    assert error.value.status_code == status
    assert error.value.code == code
    assert calls[0][calls[0].index("--container") + 1] == "trusted-container"
    assert "hostile-name.zip" not in calls[0]
    assert list(service.imports.iterdir()) == []
    if exit_code == 3:
        failures = list(service.failed.iterdir())
        assert len(failures) == 1
        assert (failures[0] / "incoming.zip").read_bytes() == b"incoming"
        assert (failures[0] / "safety" / "safety-created-by-script" / "backup.zip").is_file()
        assert str(service.root) not in error.value.message
        assert "private stdout" not in error.value.message
    else:
        assert list(service.failed.iterdir()) == []


def test_success_cleans_incoming_but_retains_private_safety(tmp_path: Path) -> None:
    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        safety = Path(argv[argv.index("--safety-out") + 1]) / "safety-ok"
        safety.mkdir()
        (safety / "backup.zip").write_bytes(b"safety")
        return _result(argv)

    service = BackupService(_settings(tmp_path), runner=runner, inspector=_running)
    service.restore(
        _site(),
        filename="backup.zip",
        confirmation="trusted-container",
        source=io.BytesIO(b"incoming"),
    )
    assert list(service.imports.iterdir()) == []
    assert (service.safety / "safety-ok" / "backup.zip").is_file()
    assert (service.safety / "safety-ok" / "backup.zip").stat().st_mode & 0o777 == 0o600


def test_purge_only_removes_expired_exports_and_imports(tmp_path: Path) -> None:
    service = BackupService(_settings(tmp_path, backup_link_ttl=30))
    service.initialize()
    export = service.exports / "old"
    incoming = service.imports / "old.zip"
    safety = service.safety / "keep"
    failed = service.failed / "keep"
    export.mkdir()
    incoming.write_bytes(b"old")
    safety.mkdir()
    failed.mkdir()
    old = time.time() - 1000
    for path in (export, incoming, safety, failed):
        os.utime(path, (old, old))

    service.purge_expired()

    assert not export.exists()
    assert not incoming.exists()
    assert safety.exists()
    assert failed.exists()


def test_download_resolution_stays_inside_one_export_directory(tmp_path: Path) -> None:
    service = BackupService(_settings(tmp_path))
    service.initialize()
    backup_id = "a" * 32
    directory = service.exports / backup_id
    directory.mkdir()
    archive = directory / "backup.zip"
    archive.write_bytes(b"zip")
    assert service.download_path(backup_id, "backup.zip") == (
        archive.resolve(),
        directory.resolve(),
    )
    assert service.download_path(backup_id, "../backup.zip") is None
    assert service.download_path("../safety", "backup.zip") is None


def test_orchestrator_does_not_duplicate_the_backup_engine() -> None:
    source = Path(BackupService.__module__.replace(".", "/") + ".py")
    checkout_source = Path(__file__).parents[1] / source.name
    text = checkout_source.read_text(encoding="utf-8").lower()
    assert "docker cp" not in text
    assert "mysqldump" not in text
    assert "mariadb-dump" not in text
