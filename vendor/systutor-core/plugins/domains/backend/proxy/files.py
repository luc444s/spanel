"""Abstracción de archivos del proxy: local, SSH o dentro del Traefik.

El transporte Docker escribe con `docker exec` en el bind mount del file
provider. Ninguna ruta puede salir de ese directorio y todo nombre pasa por
`validate_managed_path` antes de tocar el contenedor (A.SPEC 0029, I1/I2).
"""

from __future__ import annotations

import base64
import os
import shlex
import tarfile
from io import BytesIO
from pathlib import Path
from typing import Protocol

from .core import (
    EXIT_TRANSPORT,
    EXIT_VALIDATION,
    FILE_PREFIX,
    ProxyError,
    atomic_write,
    is_managed_document,
    validate_managed_path,
)


class ProxyFiles(Protocol):
    config_dir: Path
    supports_foreign_paths: bool

    def exists(self, path: Path) -> bool: ...
    def read_text(self, path: Path) -> str | None: ...
    def list_site_files(self) -> list[Path]: ...
    def read_site_files(self) -> dict[Path, str]: ...
    def write_text(self, path: Path, text: str, mode: int = 0o644) -> None: ...
    def delete(self, path: Path) -> None: ...
    def writable(self) -> tuple[bool, str]: ...


def _require_managed(path: Path, text: str | None = None) -> None:
    """Sólo los nombres gestionados se validan; el hosts del lab no lo es."""
    if path.name.startswith(FILE_PREFIX):
        validate_managed_path(path)
        if text is not None and not is_managed_document(text):
            raise ProxyError(
                f"{path.name} does not have the spanel ownership marker",
                EXIT_VALIDATION,
            )


def _unpack_snapshot(encoded: str, config_dir: Path) -> dict[Path, str]:
    """Decode a bounded tar snapshot and reject every unsafe member."""
    if not encoded.strip():
        return {}
    try:
        payload = base64.b64decode(encoded, validate=True)
        archive = tarfile.open(fileobj=BytesIO(payload), mode="r:")
    except (ValueError, tarfile.TarError) as exc:
        raise ProxyError("invalid bulk file snapshot", EXIT_TRANSPORT) from exc
    result: dict[Path, str] = {}
    with archive:
        for member in archive.getmembers():
            member_path = Path(member.name)
            if (
                member_path.is_absolute()
                or len(member_path.parts) != 1
                or member_path.name in ("", ".", "..")
                or not member.isfile()
                or not member_path.name.startswith(FILE_PREFIX)
                or member_path.suffix != ".yml"
            ):
                raise ProxyError("unsafe member in bulk file snapshot", EXIT_TRANSPORT)
            target = config_dir / member_path.name
            validate_managed_path(target)
            if target in result:
                raise ProxyError("duplicate member in bulk file snapshot", EXIT_TRANSPORT)
            stream = archive.extractfile(member)
            if stream is None:
                raise ProxyError("unreadable member in bulk file snapshot", EXIT_TRANSPORT)
            try:
                result[target] = stream.read().decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProxyError("non-UTF-8 managed document", EXIT_TRANSPORT) from exc
    return result


def _snapshot_script(config_dir: Path) -> str:
    directory = shlex.quote(str(config_dir))
    return (
        f"cd {directory} || exit 1; set -- spanel-*.yml; "
        '[ -e "$1" ] || exit 0; tar -cf - -- "$@" | base64'
    )


class LocalFiles:
    """Filesystem visible por el proceso (comportamiento de 0027)."""

    supports_foreign_paths = True

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir

    def exists(self, path: Path) -> bool:
        return path.exists()

    def read_text(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError, OSError):
            return None

    def list_site_files(self) -> list[Path]:
        if not self.config_dir.is_dir():
            return []
        return sorted(
            path
            for path in self.config_dir.iterdir()
            if path.is_file() and path.name.startswith(FILE_PREFIX) and path.suffix == ".yml"
        )

    def read_site_files(self) -> dict[Path, str]:
        return {
            path: text
            for path in self.list_site_files()
            if (text := self.read_text(path)) is not None
        }

    def write_text(self, path: Path, text: str, mode: int = 0o644) -> None:
        _require_managed(path, text)
        atomic_write(path, text, mode)

    def delete(self, path: Path) -> None:
        _require_managed(path)
        path.unlink(missing_ok=True)

    def writable(self) -> tuple[bool, str]:
        directory = self.config_dir
        if not directory.exists():
            return False, f"{directory} does not exist"
        if not os.access(directory, os.W_OK):
            return False, f"no write permission on {directory}"
        return True, str(directory)


class SshFiles:
    """Archivos remotos por SSH (comportamiento de 0027, sin cambios)."""

    supports_foreign_paths = True

    def __init__(self, runner, config_dir: Path) -> None:
        self.runner = runner
        self.config_dir = config_dir

    def exists(self, path: Path) -> bool:
        return self.runner.run(["test", "-e", str(path)]).ok

    def read_text(self, path: Path) -> str | None:
        result = self.runner.run(["cat", str(path)])
        return result.stdout if result.ok else None

    def list_site_files(self) -> list[Path]:
        result = self.runner.run(
            [
                "sh",
                "-c",
                (
                    f"ls -1 {shlex.quote(str(self.config_dir))}/spanel-*.yml "
                    "2>/dev/null || true"
                ),
            ]
        )
        return [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]

    def read_site_files(self) -> dict[Path, str]:
        result = self.runner.run(["sh", "-c", _snapshot_script(self.config_dir)])
        if not result.ok:
            raise ProxyError("SSH bulk file snapshot failed", EXIT_TRANSPORT)
        return _unpack_snapshot("".join(result.stdout.split()), self.config_dir)

    def write_text(self, path: Path, text: str, mode: int = 0o644) -> None:
        _require_managed(path, text)
        quoted = shlex.quote(str(path))
        tmp = shlex.quote(f"{path}.tmp.{os.getpid()}")
        script = f"umask 022 && cat > {tmp} && chmod {mode:o} {tmp} && mv {tmp} {quoted}"
        result = self.runner.run(["sh", "-c", script], stdin=text)
        if not result.ok:
            raise ProxyError(
                f"could not write {path}: {result.text().strip()}", EXIT_TRANSPORT
            )

    def delete(self, path: Path) -> None:
        _require_managed(path)
        result = self.runner.run(["rm", "-f", "--", str(path)])
        if not result.ok:
            raise ProxyError(f"could not delete {path.name}", EXIT_TRANSPORT)

    def writable(self) -> tuple[bool, str]:
        result = self.runner.run(["test", "-w", str(self.config_dir)])
        return result.ok, str(self.config_dir)


class DockerFiles:
    """Operaciones dentro del contenedor de Traefik con `docker exec`."""

    supports_foreign_paths = False

    def __init__(self, runner, container: str, config_dir: Path) -> None:
        self.runner = runner
        self.container = container
        self.config_dir = config_dir

    # ---- helpers ---------------------------------------------------------

    def _exec(self, *args: str, stdin: str | None = None):
        argv = ["docker", "exec", self.container, *args]
        try:
            return self.runner.run(argv, stdin=stdin)
        except OSError as exc:
            raise ProxyError(
                f"docker exec in {self.container} is not available: {exc}",
                EXIT_TRANSPORT,
            ) from exc

    def _exec_input(self, script: str, text: str):
        argv = ["docker", "exec", "-i", self.container, "sh", "-c", script]
        try:
            return self.runner.run(argv, stdin=text)
        except OSError as exc:
            raise ProxyError(
                f"docker exec in {self.container} is not available: {exc}",
                EXIT_TRANSPORT,
            ) from exc

    def _checked(self, path: Path) -> str:
        target = Path(path)
        try:
            relative = target.relative_to(self.config_dir)
        except ValueError as exc:
            raise ProxyError(
                f"{target.name} falls outside the file provider directory",
                EXIT_TRANSPORT,
            ) from exc
        if not relative.parts or any(part in ("", "..") for part in relative.parts):
            raise ProxyError(
                f"{target.name} falls outside the file provider directory",
                EXIT_TRANSPORT,
            )
        validate_managed_path(target)
        return str(target)

    def _fail(self, action: str, result) -> ProxyError:
        detail = result.text().strip() or "no details"
        return ProxyError(
            f"docker exec ({action}) in {self.container} failed: {detail}",
            EXIT_TRANSPORT,
        )

    # ---- ProxyFiles ------------------------------------------------------

    def exists(self, path: Path) -> bool:
        result = self._exec("test", "-e", self._checked(path))
        if result.ok:
            return True
        if result.returncode == 1:
            return False
        raise self._fail("test -e", result)

    def read_text(self, path: Path) -> str | None:
        result = self._exec("cat", self._checked(path))
        if result.ok:
            return result.stdout
        return None

    def list_site_files(self) -> list[Path]:
        script = (
            f"ls -1 {shlex.quote(str(self.config_dir))}/spanel-*.yml "
            "2>/dev/null || true"
        )
        result = self._exec("sh", "-c", script)
        if not result.ok:
            raise self._fail("ls", result)
        found: list[Path] = []
        for line in result.stdout.splitlines():
            name = line.strip()
            if not name:
                continue
            path = Path(name)
            if path.name.startswith(FILE_PREFIX) and path.suffix == ".yml":
                found.append(path)
        return sorted(found)

    def read_site_files(self) -> dict[Path, str]:
        result = self._exec("sh", "-c", _snapshot_script(self.config_dir))
        if not result.ok:
            raise self._fail("bulk snapshot", result)
        return _unpack_snapshot("".join(result.stdout.split()), self.config_dir)

    def write_text(self, path: Path, text: str, mode: int = 0o644) -> None:
        target = self._checked(path)
        _require_managed(Path(target), text)
        tmp = str(self.config_dir / f".{Path(target).name}.tmp.{os.getpid()}")
        script = (
            f"umask 022 && cat > {shlex.quote(tmp)}"
            f" && chmod {format(mode, '04o')} {shlex.quote(tmp)}"
            f" && mv {shlex.quote(tmp)} {shlex.quote(target)}"
        )
        result = self._exec_input(script, text)
        if not result.ok:
            raise self._fail("write", result)
        if self.read_text(Path(target)) != text:
            raise ProxyError(
                f"rereading {Path(target).name} in {self.container} does not match",
                EXIT_TRANSPORT,
            )

    def delete(self, path: Path) -> None:
        result = self._exec("rm", "-f", "--", self._checked(path))
        if not result.ok:
            raise self._fail("rm", result)

    def writable(self) -> tuple[bool, str]:
        result = self._exec("test", "-w", str(self.config_dir))
        if result.ok:
            return True, f"{self.config_dir} inside {self.container}"
        return False, f"{self.config_dir} is not writable inside {self.container}"


def build_files(runner, detection) -> ProxyFiles:
    if detection.transport == "docker":
        return DockerFiles(
            runner, detection.traefik_container or "", detection.config_dir
        )
    if detection.transport == "ssh":
        return SshFiles(runner, detection.config_dir)
    return LocalFiles(detection.config_dir)
