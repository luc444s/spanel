"""Operaciones exec del transporte Docker (A.SPEC 0029, V2)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from plugins.domains.backend.proxy.core import (
    EXIT_TRANSPORT,
    EXIT_VALIDATION,
    ProxyError,
    render_site_yaml,
)
from plugins.domains.backend.proxy.files import DockerFiles, LocalFiles
from plugins.domains.backend.proxy.transport import CommandResult

CONTAINER = "traefik"
CONFIG = Path("/config")


class FakeExec:
    """Simula el filesystem del contenedor sin Docker."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.calls: list[tuple[list[str], str | None]] = []
        self.write_code = 0
        self.write_error = "boom"
        self.rewrite: str | None = None
        self.writable = True

    def run(self, argv, *, check=False, stdin=None, timeout=None):
        argv = list(argv)
        self.calls.append((argv, stdin))

        def result(code: int = 0, out: str = "", err: str = "") -> CommandResult:
            return CommandResult(argv, code, out, err)

        if argv[:2] != ["docker", "exec"]:
            return result(127, "", "comando inesperado")
        rest = argv[2:]
        if rest[:1] == ["-i"]:
            rest = rest[1:]
        assert rest[0] == CONTAINER
        command = rest[1:]
        if command[0] == "sh" and command[1] == "-c":
            return self._shell(command[2], stdin, result)
        if command[0] == "cat":
            path = command[1]
            if path in self.files:
                return result(0, self.files[path])
            return result(1, "", "no such file")
        if command[0] == "test":
            if command[1] == "-w":
                return result(0 if self.writable else 1)
            return result(0 if command[2] in self.files else 1)
        if command[0] == "rm":
            self.files.pop(command[-1], None)
            return result(0)
        return result(0)

    def _shell(self, script: str, stdin: str | None, result) -> CommandResult:
        if script.startswith("ls -1 "):
            directory = script.split("ls -1 ", 1)[1].split("/spanel-*.yml", 1)[0]
            prefix = directory.rstrip("/") + "/spanel-"
            names = sorted(
                path
                for path in self.files
                if path.startswith(prefix) and path.endswith(".yml")
            )
            return result(0, "".join(f"{name}\n" for name in names))
        tmp_match = re.search(r"cat > (\S+)", script)
        mv_match = re.search(r"&& mv (\S+) (\S+)$", script)
        assert tmp_match and mv_match, script
        tmp, target = tmp_match.group(1), mv_match.group(2)
        if self.write_code:
            return result(self.write_code, "", self.write_error)
        value = stdin or ""
        self.files[target] = value if self.rewrite is None else self.rewrite
        self.files.pop(tmp, None)
        return result(0)


def make_files() -> tuple[DockerFiles, FakeExec]:
    runner = FakeExec()
    return DockerFiles(runner, CONTAINER, CONFIG), runner


def managed(container: str = "spanel-lucas-wp", domain: str = "lucas.spanel") -> str:
    return render_site_yaml(container, [domain], "letsencrypt")


# ---- listar / leer / existe ------------------------------------------------


def test_list_zero_one_and_many_ignoring_foreign() -> None:
    files, runner = make_files()
    assert files.list_site_files() == []

    runner.files["/config/spanel-b.yml"] = managed("spanel-b-wp", "b.spanel")
    assert files.list_site_files() == [Path("/config/spanel-b.yml")]

    runner.files["/config/spanel-a.yml"] = managed("spanel-a-wp", "a.spanel")
    runner.files["/config/other.yml"] = "http: {}\n"
    runner.files["/config/notes.txt"] = "hola"
    assert files.list_site_files() == [
        Path("/config/spanel-a.yml"),
        Path("/config/spanel-b.yml"),
    ]
    argv, _ = runner.calls[-1]
    assert argv[:4] == ["docker", "exec", CONTAINER, "sh"]
    assert "spanel-*.yml" in argv[-1]


def test_read_and_exists_managed_and_foreign() -> None:
    files, runner = make_files()
    runner.files["/config/spanel-a.yml"] = managed()
    assert files.exists(Path("/config/spanel-a.yml")) is True
    assert files.read_text(Path("/config/spanel-a.yml")) == managed()
    assert files.exists(Path("/config/spanel-missing.yml")) is False

    with pytest.raises(ProxyError) as exc:
        files.exists(Path("/config/other.yml"))
    assert exc.value.exit_code == EXIT_VALIDATION
    with pytest.raises(ProxyError):
        files.delete(Path("/config/other.yml"))


# ---- escribir --------------------------------------------------------------


def test_write_builds_exec_argv_and_sends_stdin() -> None:
    files, runner = make_files()
    text = managed()
    files.write_text(CONFIG / "spanel-lucas.yml", text)

    argv, stdin = runner.calls[0]
    assert argv[:5] == ["docker", "exec", "-i", CONTAINER, "sh"]
    assert argv[5] == "-c"
    script = argv[6]
    assert "umask 022" in script
    assert "cat > /config/.spanel-lucas.yml.tmp." in script
    assert "chmod 0644" in script
    assert "mv /config/.spanel-lucas.yml.tmp." in script
    assert script.endswith("/config/spanel-lucas.yml")
    assert "lucas.spanel" not in " ".join(argv), "el YAML no viaja por argv"
    assert stdin == text
    assert runner.files["/config/spanel-lucas.yml"] == text
    # La relectura ocurre después del mv.
    assert any(call[0][:4] == ["docker", "exec", CONTAINER, "cat"] for call in runner.calls)


def test_write_rejects_unmanaged_content() -> None:
    files, runner = make_files()
    with pytest.raises(ProxyError):
        files.write_text(CONFIG / "spanel-x.yml", "http: {}\n")
    assert runner.calls == []


def test_write_refuses_paths_outside_provider() -> None:
    files, runner = make_files()
    with pytest.raises(ProxyError) as exc:
        files.write_text(Path("/etc/spanel-x.yml"), managed())
    assert exc.value.exit_code == EXIT_TRANSPORT
    with pytest.raises(ProxyError):
        files.write_text(CONFIG / ".." / "eta" / "spanel-x.yml", managed())
    assert runner.calls == []


def test_rewrite_mismatch_fails_without_success() -> None:
    files, runner = make_files()
    runner.rewrite = "otra cosa\n"
    with pytest.raises(ProxyError) as exc:
        files.write_text(CONFIG / "spanel-x.yml", managed())
    assert exc.value.exit_code == EXIT_TRANSPORT
    assert "no coincide" in str(exc.value)


def test_write_exec_failure_is_transport_error_without_retry() -> None:
    files, runner = make_files()
    runner.write_code = 1
    with pytest.raises(ProxyError) as exc:
        files.write_text(CONFIG / "spanel-x.yml", managed())
    assert exc.value.exit_code == EXIT_TRANSPORT
    containers = {
        call[0][3] for call in runner.calls if call[0][:2] == ["docker", "exec"]
    }
    assert containers == {CONTAINER}


# ---- borrar y escribible ---------------------------------------------------


def test_delete_managed_file() -> None:
    files, runner = make_files()
    runner.files["/config/spanel-a.yml"] = managed()
    files.delete(Path("/config/spanel-a.yml"))
    assert "/config/spanel-a.yml" not in runner.files
    argv, _ = runner.calls[-1]
    assert argv[:5] == ["docker", "exec", CONTAINER, "rm", "-f"]
    assert argv[-1] == "/config/spanel-a.yml"


def test_writable_reports_container_path() -> None:
    files, runner = make_files()
    ok, detail = files.writable()
    assert ok is True
    assert detail == "/config dentro de traefik"
    runner.writable = False
    ok, detail = files.writable()
    assert ok is False
    assert "no es escribible" in detail


# ---- regresión local -------------------------------------------------------


def test_local_files_keep_0027_behavior(tmp_path: Path) -> None:
    files = LocalFiles(tmp_path)
    text = managed()
    files.write_text(tmp_path / "spanel-a.yml", text)
    assert files.exists(tmp_path / "spanel-a.yml") is True
    assert files.read_text(tmp_path / "spanel-a.yml") == text
    assert files.list_site_files() == [tmp_path / "spanel-a.yml"]
    ok, _ = files.writable()
    assert ok is True
    files.delete(tmp_path / "spanel-a.yml")
    assert files.exists(tmp_path / "spanel-a.yml") is False
