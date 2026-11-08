"""Tests offline del restore round-trip de A.SPEC 0048."""

from __future__ import annotations

import hashlib
import io
import json
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import site_backup as backup
import site_restore as restore


def make_zip(
    tmp_path: Path,
    *,
    name: str = "incoming.zip",
    kind: str = "static",
    container: str = "sitio",
    site: bytes = b"VERSION RESPALDADA",
    sql: bytes = b"RESTORE SQL;\n",
    db_host: str = "db:host-del-manifest-no-confiable",
    extra: dict[str, bytes] | None = None,
) -> Path:
    files = {"site/html/index.html": site}
    if kind == "wp":
        files["database/backup.sql"] = sql
    files.update(extra or {})
    entries = {
        path: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        for path, data in files.items()
    }
    manifest = {
        "kind": kind,
        "container": container,
        "timestamp": "20261107-040000",
        "db_host": db_host if kind == "wp" else None,
        "entries": entries,
        "total_size": sum(len(data) for data in files.values()),
    }
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zipped:
        for arcname, data in files.items():
            zipped.writestr(arcname, data)
        zipped.writestr("manifest.json", json.dumps(manifest))
    return path


def inspect_json(container: str, *, kind: str = "static", running: bool = True) -> str:
    env = []
    if kind == "wp":
        env = [
            "WORDPRESS_DB_HOST=current-db",
            "WORDPRESS_DB_USER=current-user",
            "WORDPRESS_DB_PASSWORD=current-secret",
            "WORDPRESS_DB_NAME=current-site-db",
        ]
    return json.dumps(
        [
            {
                "Name": f"/{container}",
                "Config": {"Env": env, "Labels": {}},
                "State": {"Running": running},
            }
        ]
    )


class FakeDocker:
    def __init__(
        self,
        *,
        container: str = "sitio",
        kind: str = "static",
        running: bool = True,
        fail_restore_sql: bool = False,
        fail_safety_sql: bool = False,
        events: list[str] | None = None,
    ):
        self.container = container
        self.kind = kind
        self.running = running
        self.fail_restore_sql = fail_restore_sql
        self.fail_safety_sql = fail_safety_sql
        self.events = events if events is not None else []
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.inputs: list[bytes] = []

    def __call__(self, args: list[str], input_bytes: bytes | None) -> backup.Result:
        self.calls.append((list(args), input_bytes))
        if args[0] == "inspect":
            if args[1] == self.container:
                return backup.Result(inspect_json(self.container, kind=self.kind, running=self.running))
            return backup.Result("", f"No such object: {args[1]}", 1)
        if args[0] == "cp":
            self.events.append("mutation:cp")
            return backup.Result()
        if args[0] == "exec":
            command = args[-1] if args[-2:-1] == ["-c"] else ""
            if "SHOW FULL TABLES" in command:
                assert "current-db" in args
                return backup.Result("wp_posts\tBASE TABLE\nwp_view\tVIEW\n")
            if input_bytes is not None:
                self.inputs.append(input_bytes)
                self.events.append("mutation:db")
                if input_bytes == b"RESTORE SQL;\n" and self.fail_restore_sql:
                    return backup.Result("", "current-secret SQL restore exploded", 1)
                if input_bytes == b"SAFETY SQL;\n" and self.fail_safety_sql:
                    return backup.Result("", "current-secret rollback exploded", 1)
            elif any(word in command for word in ("find ", ".maintenance", "spanel-restore")):
                self.events.append("mutation:files")
            return backup.Result()
        return backup.Result("", f"verb inesperado: {args[0]}", 127)


def tooling(
    fake: FakeDocker,
    safety: Path | Exception | None = None,
    events: list[str] | None = None,
) -> restore.Tooling:
    def safety_backup(_args, _tooling) -> Path:
        if events is not None:
            events.append("safety")
        if isinstance(safety, Exception):
            raise safety
        assert isinstance(safety, Path)
        return safety

    return restore.Tooling(
        docker=fake,
        now=lambda: 1_700_000_000.0,
        safety_backup=safety_backup if safety is not None else None,
        token_hex=lambda _n: "abc123",
    )


def argv(path: Path, *, container: str = "sitio", yes: bool = False) -> list[str]:
    result = ["--zip", str(path), "--container", container]
    if yes:
        result.append("--yes")
    return result


def mutating_calls(fake: FakeDocker) -> list[list[str]]:
    return [args for args, _ in fake.calls if args[0] in {"cp", "exec"}]


def docker_cp_tar(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(f"html/{name}")
            info.size = len(data)
            info.mtime = 1_700_000_000
            tar.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def test_without_yes_is_verified_plan_with_zero_mutations(tmp_path: Path, capsys):
    incoming = make_zip(tmp_path)
    fake = FakeDocker()
    code = restore.main(argv(incoming), tooling(fake, safety=None))
    assert code == restore.EXIT_OK
    assert not mutating_calls(fake)
    assert "sin --yes" in capsys.readouterr().out


def test_confirmed_static_restore_creates_safety_before_mutation(tmp_path: Path):
    incoming = make_zip(tmp_path, site=b"incoming")
    safety = make_zip(tmp_path, name="safety.zip", site=b"previous")
    events: list[str] = []
    fake = FakeDocker(events=events)
    code = restore.main(argv(incoming, yes=True), tooling(fake, safety, events))
    assert code == restore.EXIT_OK
    assert events[0] == "safety"
    assert "mutation:cp" in events
    commands = "\n".join(" ".join(args) for args, _ in fake.calls)
    assert "/usr/share/nginx/html" in commands
    assert "find /usr/share/nginx/html" in commands


def test_safety_failure_blocks_every_mutation(tmp_path: Path):
    incoming = make_zip(tmp_path)
    fake = FakeDocker()
    code = restore.main(
        argv(incoming, yes=True),
        tooling(fake, restore.RestoreError("no se pudo respaldar")),
    )
    assert code == restore.EXIT_ERROR
    assert not mutating_calls(fake)


def test_injected_invalid_safety_is_rejected_before_mutation(tmp_path: Path):
    incoming = make_zip(tmp_path)
    invalid = tmp_path / "invalid-safety.zip"
    invalid.write_bytes(b"not a zip")
    fake = FakeDocker()
    assert restore.main(argv(incoming, yes=True), tooling(fake, invalid)) == restore.EXIT_VERIFY
    assert not mutating_calls(fake)


def test_default_safety_backup_uses_0047_format(tmp_path: Path):
    class BackupDocker(FakeDocker):
        def __call__(self, args: list[str], input_bytes: bytes | None) -> backup.Result:
            if args[0] == "cp":
                self.calls.append((list(args), input_bytes))
                return backup.Result(stdout_bytes=docker_cp_tar({"index.html": b"estado actual"}))
            return super().__call__(args, input_bytes)

    incoming = make_zip(tmp_path)
    args = restore.build_parser().parse_args(
        [
            "--zip",
            str(incoming),
            "--container",
            "sitio",
            "--safety-out",
            str(tmp_path / "safety-root"),
        ]
    )
    fake = BackupDocker()
    result = restore.create_safety_backup(args, tooling(fake))
    assert result.is_file()
    assert backup.verify_zip(result)["container"] == "sitio"


@pytest.mark.parametrize(
    ("zip_kind", "target_kind", "zip_container", "target_container"),
    [
        ("static", "static", "otro", "sitio"),
        ("wp", "static", "sitio", "sitio"),
    ],
)
def test_container_or_kind_mismatch_fails_before_mutation(
    tmp_path: Path, zip_kind: str, target_kind: str, zip_container: str, target_container: str
):
    incoming = make_zip(tmp_path, kind=zip_kind, container=zip_container)
    fake = FakeDocker(container=target_container, kind=target_kind)
    assert restore.main(argv(incoming, container=target_container, yes=True), tooling(fake)) == 1
    assert not mutating_calls(fake)


def test_strict_zip_rejects_entry_not_declared(tmp_path: Path):
    valid = make_zip(tmp_path)
    broken = tmp_path / "extra.zip"
    with zipfile.ZipFile(valid) as source, zipfile.ZipFile(broken, "w") as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        target.writestr("sorpresa.txt", b"fuera del manifest")
    fake = FakeDocker()
    assert restore.main(argv(broken), tooling(fake)) == restore.EXIT_VERIFY
    assert not fake.calls


def test_manifest_larger_than_one_mib_is_accepted(tmp_path: Path):
    path = tmp_path / "large-manifest.zip"
    manifest = {
        "kind": "static",
        "container": "sitio",
        "entries": {},
        "padding": "x" * (1024 * 1024),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("manifest.json", json.dumps(manifest))

    with zipfile.ZipFile(path) as zipped:
        assert restore._read_manifest(zipped)["container"] == "sitio"


def test_manifest_over_bounded_limit_is_rejected(tmp_path: Path):
    path = tmp_path / "oversized-manifest.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("manifest.json", b"x" * (restore.MANIFEST_MAX_BYTES + 1))

    with (
        zipfile.ZipFile(path) as zipped,
        pytest.raises(restore.StrictVerifyError, match="demasiado grande"),
    ):
        restore._read_manifest(zipped)


@pytest.mark.parametrize("unsafe", ["site/html/../escape", "/site/html/escape", "site\\html\\x"])
def test_strict_zip_rejects_traversal_or_noncanonical_names(tmp_path: Path, unsafe: str):
    broken = make_zip(tmp_path, extra={unsafe: b"bad"})
    fake = FakeDocker()
    assert restore.main(argv(broken), tooling(fake)) == restore.EXIT_VERIFY
    assert not fake.calls


def test_strict_zip_rejects_symlink(tmp_path: Path):
    path = tmp_path / "symlink.zip"
    data = b"target"
    entry = zipfile.ZipInfo("site/html/link")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    manifest = {
        "kind": "static",
        "container": "sitio",
        "timestamp": "x",
        "db_host": None,
        "entries": {
            entry.filename: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        },
        "total_size": len(data),
    }
    with zipfile.ZipFile(path, "w") as zipped:
        zipped.writestr(entry, data)
        zipped.writestr("manifest.json", json.dumps(manifest))
    assert restore.main(argv(path), tooling(FakeDocker())) == restore.EXIT_VERIFY


def test_wordpress_restore_uses_current_single_database_not_manifest(tmp_path: Path):
    incoming = make_zip(tmp_path, kind="wp", container="wp-site", sql=b"RESTORE SQL;\n")
    safety = make_zip(
        tmp_path,
        name="safety.zip",
        kind="wp",
        container="wp-site",
        sql=b"SAFETY SQL;\n",
    )
    fake = FakeDocker(container="wp-site", kind="wp")
    code = restore.main(argv(incoming, container="wp-site", yes=True), tooling(fake, safety))
    assert code == restore.EXIT_OK
    all_commands = "\n".join(" ".join(args) for args, _ in fake.calls)
    assert "current-db" in all_commands
    assert "host-del-manifest-no-confiable" not in all_commands
    assert "--all-databases" not in all_commands
    assert b"RESTORE SQL;\n" in fake.inputs
    drop = next(value for value in fake.inputs if b"DROP TABLE" in value)
    assert b"`wp_posts`" in drop
    assert b"DROP VIEW IF EXISTS `wp_view`" in drop


def test_sql_failure_rolls_back_with_safety_zip_and_redacts_secret(tmp_path: Path, capsys):
    incoming = make_zip(tmp_path, kind="wp", container="wp-site", sql=b"RESTORE SQL;\n")
    safety = make_zip(
        tmp_path,
        name="safety.zip",
        kind="wp",
        container="wp-site",
        sql=b"SAFETY SQL;\n",
    )
    fake = FakeDocker(container="wp-site", kind="wp", fail_restore_sql=True)
    code = restore.main(argv(incoming, container="wp-site", yes=True), tooling(fake, safety))
    assert code == restore.EXIT_ERROR
    assert b"RESTORE SQL;\n" in fake.inputs
    assert b"SAFETY SQL;\n" in fake.inputs
    output = capsys.readouterr()
    assert "ROLLBACK: estado previo restaurado" in output.err
    assert "current-secret" not in output.out + output.err


def test_failed_rollback_has_distinct_exit_code(tmp_path: Path):
    incoming = make_zip(tmp_path, kind="wp", container="wp-site", sql=b"RESTORE SQL;\n")
    safety = make_zip(
        tmp_path,
        name="safety.zip",
        kind="wp",
        container="wp-site",
        sql=b"SAFETY SQL;\n",
    )
    fake = FakeDocker(
        container="wp-site",
        kind="wp",
        fail_restore_sql=True,
        fail_safety_sql=True,
    )
    code = restore.main(argv(incoming, container="wp-site", yes=True), tooling(fake, safety))
    assert code == restore.EXIT_ROLLBACK_FAILED
