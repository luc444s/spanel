#!/usr/bin/env python3
"""Restaura el mismo ZIP verificable producido por site_backup.py.

Antes de mutar crea un safety backup del estado actual. El ZIP fuente se valida
de forma estricta y se extrae por streaming; nunca se confían container, DB ni
paths del manifest para seleccionar el destino. En WordPress las credenciales se
resuelven otra vez desde el contenedor actual. Si el apply falla se intenta
rollback automático con el safety ZIP.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import site_backup as backup

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_VERIFY = 2
EXIT_ROLLBACK_FAILED = 3
DEFAULT_MAX_BYTES = 20 * 1024 * 1024 * 1024
MANIFEST_MAX_BYTES = 1024 * 1024
CHUNK_SIZE = 1024 * 1024


class RestoreError(Exception):
    """Fallo operacional o precondición inválida."""


class StrictVerifyError(RestoreError):
    """El ZIP no cumple exactamente el contrato restaurable."""


class ApplyError(RestoreError):
    def __init__(self, message: str, *, mutated: bool):
        super().__init__(message)
        self.mutated = mutated


@dataclass(frozen=True)
class RestorePackage:
    zip_path: Path
    manifest: dict
    site_dir: Path
    sql_path: Path | None


@dataclass
class Tooling:
    docker: Callable[[list[str], bytes | None], backup.Result]
    now: Callable[[], float]
    safety_backup: Callable[[argparse.Namespace, Tooling], Path] | None = None
    token_hex: Callable[[int], str] = secrets.token_hex


def docker_cli(args: list[str], input_bytes: bytes | None = None) -> backup.Result:
    """Docker binario: tar/SQL nunca pasan por una transcodificación de texto."""
    try:
        result = subprocess.run(
            ["docker", *args],
            input=input_bytes,
            capture_output=True,
            timeout=900,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return backup.Result("", f"docker CLI indisponible: {exc}", 127)
    return backup.Result(
        stdout=result.stdout[:16_384].decode("utf-8", errors="replace"),
        stderr=result.stderr.decode("utf-8", errors="replace"),
        returncode=result.returncode,
        stdout_bytes=result.stdout,
    )


def default_tooling() -> Tooling:
    import time

    return Tooling(docker=docker_cli, now=time.time)


def _backup_tooling(tooling: Tooling) -> backup.Tooling:
    def call(args: list[str]) -> backup.Result:
        return tooling.docker(args, None)

    return backup.Tooling(docker=call, docker_bytes=call, now=tooling.now)


def _safe_member_name(name: str) -> tuple[str, ...]:
    if not name or "\x00" in name or "\\" in name or name.startswith("/") or name.endswith("/"):
        raise StrictVerifyError(f"entry insegura: {name!r}")
    raw_parts = name.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise StrictVerifyError(f"entry insegura: {name!r}")
    parts = PurePosixPath(name).parts
    if tuple(raw_parts) != parts:
        raise StrictVerifyError(f"entry no canónica: {name!r}")
    return parts


def _read_manifest(zipped: zipfile.ZipFile) -> dict:
    infos = zipped.infolist()
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise StrictVerifyError("ZIP con entries duplicadas")
    if names.count("manifest.json") != 1:
        raise StrictVerifyError("ZIP sin un único manifest.json")
    info = zipped.getinfo("manifest.json")
    if info.file_size > MANIFEST_MAX_BYTES:
        raise StrictVerifyError("manifest.json demasiado grande")
    try:
        manifest = json.loads(zipped.read(info).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StrictVerifyError(f"manifest.json inválido: {exc}") from exc
    if not isinstance(manifest, dict):
        raise StrictVerifyError("manifest.json debe ser un objeto")
    return manifest


def _strict_extract(zip_path: Path, destination: Path, max_bytes: int) -> RestorePackage:
    """Validate exact archive surface and stream verified files into destination."""
    try:
        resolved = zip_path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise StrictVerifyError(f"no existe el ZIP: {zip_path}") from exc
    if not resolved.is_file() or resolved.suffix.lower() != ".zip":
        raise StrictVerifyError(f"no es un .zip: {resolved}")

    try:
        with zipfile.ZipFile(resolved) as zipped:
            manifest = _read_manifest(zipped)
            kind = manifest.get("kind")
            container = manifest.get("container")
            entries = manifest.get("entries")
            if kind not in {"wp", "static"}:
                raise StrictVerifyError(f"kind inválido: {kind!r}")
            if not isinstance(container, str) or not container:
                raise StrictVerifyError("manifest sin container")
            if not isinstance(entries, dict) or not entries:
                raise StrictVerifyError("manifest sin entries")

            expected_names = set(entries) | {"manifest.json"}
            actual_names = {info.filename for info in zipped.infolist()}
            extra = sorted(actual_names - expected_names)
            missing = sorted(expected_names - actual_names)
            if extra or missing:
                raise StrictVerifyError(f"surface distinta al manifest; extra={extra}, missing={missing}")

            site_roots: set[str] = set()
            declared_total = 0
            infos: list[tuple[zipfile.ZipInfo, tuple[str, ...], dict]] = []
            for name, meta in entries.items():
                if not isinstance(name, str) or not isinstance(meta, dict):
                    raise StrictVerifyError("entry/meta inválida en manifest")
                parts = _safe_member_name(name)
                if parts[0] == "site":
                    if len(parts) < 3:
                        raise StrictVerifyError(f"entry site sin raíz docker-cp: {name}")
                    site_roots.add(parts[1])
                elif tuple(parts) != ("database", "backup.sql"):
                    raise StrictVerifyError(f"entry fuera de site/database: {name}")
                info = zipped.getinfo(name)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise StrictVerifyError(f"symlink prohibido: {name}")
                file_type = stat.S_IFMT(mode)
                if file_type not in {0, stat.S_IFREG}:
                    raise StrictVerifyError(f"tipo de entry no regular: {name}")
                if info.flag_bits & 0x1:
                    raise StrictVerifyError(f"entry cifrada no soportada: {name}")
                try:
                    declared_size = int(meta["size"])
                    expected_hash = str(meta["sha256"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise StrictVerifyError(f"metadata inválida: {name}") from exc
                if declared_size < 0 or declared_size != info.file_size:
                    raise StrictVerifyError(f"size mismatch: {name}")
                if len(expected_hash) != 64:
                    raise StrictVerifyError(f"SHA256 inválido: {name}")
                declared_total += declared_size
                if declared_total > max_bytes:
                    raise StrictVerifyError(f"ZIP supera --max-bytes ({max_bytes})")
                infos.append((info, parts, meta))

            if len(site_roots) != 1:
                raise StrictVerifyError(f"se esperaba una raíz site, encontradas: {sorted(site_roots)}")
            has_sql = "database/backup.sql" in entries
            if kind == "wp" and not has_sql:
                raise StrictVerifyError("WordPress sin database/backup.sql")
            if kind == "static" and has_sql:
                raise StrictVerifyError("estático no debe contener database/backup.sql")
            try:
                manifest_total = int(manifest.get("total_size", -1))
            except (TypeError, ValueError) as exc:
                raise StrictVerifyError("manifest total_size inválido") from exc
            if manifest_total != declared_total:
                raise StrictVerifyError("manifest total_size no coincide con entries")

            destination.mkdir(parents=True, mode=0o700, exist_ok=True)
            site_dir = destination / "site"
            sql_path: Path | None = None
            for info, parts, meta in infos:
                if parts[0] == "site":
                    relative = Path(*parts[2:])  # quita site/<basename docker-cp>/
                    output = site_dir / relative
                else:
                    output = destination / "database" / "backup.sql"
                    sql_path = output
                output.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                written = 0
                with zipped.open(info) as source, output.open("xb") as target:
                    for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                        written += len(chunk)
                        if written > int(meta["size"]):
                            raise StrictVerifyError(f"entry expandió más de lo declarado: {info.filename}")
                        digest.update(chunk)
                        target.write(chunk)
                if written != int(meta["size"]) or digest.hexdigest() != meta["sha256"]:
                    raise StrictVerifyError(f"hash/size mismatch: {info.filename}")
                permissions = mode & 0o777
                if permissions:
                    os.chmod(output, permissions)

            if not any(path.is_file() for path in site_dir.rglob("*")):
                raise StrictVerifyError("ZIP sin archivos restaurables en site/")
            if sql_path is not None and not sql_path.read_bytes().strip():
                raise StrictVerifyError("database/backup.sql vacío")
            return RestorePackage(resolved, manifest, site_dir, sql_path)
    except zipfile.BadZipFile as exc:
        raise StrictVerifyError(f"ZIP corrupto: {exc}") from exc


def _target(args: argparse.Namespace, tooling: Tooling) -> backup.Target:
    adapter = _backup_tooling(tooling)
    try:
        target = backup.build_target(adapter, args.container, args)
        inspected = backup.inspect_container(adapter, args.container)
    except backup.BackupError as exc:
        raise RestoreError(str(exc)) from exc
    if not bool(inspected.get("State", {}).get("Running")):
        raise RestoreError(f"el contenedor {args.container} no está corriendo")
    path = PurePosixPath(target.site_path)
    if not target.site_path.startswith("/") or ".." in path.parts or target.site_path == "/":
        raise RestoreError(f"path de destino inseguro: {target.site_path!r}")
    return target


def _check_package_target(package: RestorePackage, target: backup.Target) -> None:
    if package.manifest["container"] != target.container:
        raise RestoreError(
            f"el ZIP pertenece a {package.manifest['container']!r}, no a {target.container!r}"
        )
    if package.manifest["kind"] != target.kind:
        raise RestoreError(
            f"kind del ZIP {package.manifest['kind']!r} != kind actual {target.kind!r}"
        )


def _detail(result: backup.Result, secrets_to_hide: tuple[str, ...] = ()) -> str:
    text = (result.stderr or result.stdout or f"exit {result.returncode}").strip()[:600]
    for secret in secrets_to_hide:
        if secret:
            text = text.replace(secret, "***")
    return text


def _run(
    tooling: Tooling,
    args: list[str],
    *,
    input_bytes: bytes | None = None,
    label: str,
    secrets_to_hide: tuple[str, ...] = (),
) -> backup.Result:
    result = tooling.docker(args, input_bytes)
    if result.returncode != 0:
        raise RestoreError(f"{label} falló: {_detail(result, secrets_to_hide)}")
    return result


def _db_command(target: backup.Target, client: str, extra: list[str]) -> str:
    values = [
        client,
        f"-u{target.db_user or ''}",
        f"-p{target.db_pass or ''}",
        *extra,
        target.db_name or "",
    ]
    return "exec " + " ".join(shlex.quote(value) for value in values)


def _db_call(
    tooling: Tooling,
    target: backup.Target,
    *,
    extra: list[str],
    input_bytes: bytes | None = None,
    label: str,
) -> backup.Result:
    if not all((target.db_container, target.db_user, target.db_name, target.db_pass)):
        raise RestoreError("target WordPress sin credenciales/DB container completos")
    last: backup.Result | None = None
    for client in ("mariadb", "mysql"):
        command = _db_command(target, client, extra)
        docker_args = ["exec"]
        if input_bytes is not None:
            docker_args.append("-i")
        docker_args += [target.db_container or "", "sh", "-c", command]
        result = tooling.docker(docker_args, input_bytes)
        last = result
        if result.returncode == 0:
            return result
        detail = _detail(result, (target.db_pass or "",))
        if "not found" not in detail.lower():
            raise RestoreError(f"{label} falló: {detail}")
    assert last is not None
    raise RestoreError(f"{label} falló: {_detail(last, (target.db_pass or '',))}")


def _drop_current_database(tooling: Tooling, target: backup.Target) -> None:
    listed = _db_call(
        tooling,
        target,
        extra=["-N", "-B", "-e", "SHOW FULL TABLES"],
        label="listar tablas actuales",
    )
    views: list[str] = []
    tables: list[str] = []
    for raw in listed.stdout.splitlines():
        columns = raw.split("\t")
        if not columns or not columns[0]:
            continue
        name = columns[0].replace("`", "``")
        if len(columns) > 1 and columns[1].upper() == "VIEW":
            views.append(name)
        else:
            tables.append(name)
    statements = ["SET FOREIGN_KEY_CHECKS=0;"]
    statements += [f"DROP VIEW IF EXISTS `{name}`;" for name in views]
    statements += [f"DROP TABLE IF EXISTS `{name}`;" for name in tables]
    statements.append("SET FOREIGN_KEY_CHECKS=1;")
    _db_call(
        tooling,
        target,
        extra=[],
        input_bytes=("\n".join(statements) + "\n").encode(),
        label="vaciar la DB del sitio",
    )


def _restore_database(tooling: Tooling, target: backup.Target, sql_path: Path) -> None:
    _drop_current_database(tooling, target)
    _db_call(
        tooling,
        target,
        extra=[],
        input_bytes=sql_path.read_bytes(),
        label="importar database/backup.sql",
    )


def _apply(package: RestorePackage, target: backup.Target, tooling: Tooling) -> None:
    stage = f"/tmp/spanel-restore-{tooling.token_hex(8)}"
    q_stage = shlex.quote(stage)
    q_target = shlex.quote(target.site_path)
    mutated = False
    try:
        _run(
            tooling,
            ["exec", target.container, "sh", "-c", f"rm -rf -- {q_stage} && mkdir -p -- {q_stage}"],
            label="preparar staging",
        )
        _run(
            tooling,
            ["cp", f"{package.site_dir}/.", f"{target.container}:{stage}/"],
            label="copiar archivos al staging",
        )
        if target.kind == "wp":
            mutated = True
            marker = shlex.quote(f"{target.site_path.rstrip('/')}/.maintenance")
            _run(
                tooling,
                [
                    "exec",
                    target.container,
                    "sh",
                    "-c",
                    f"printf '%s\\n' '<?php $upgrading = time(); ?>' > {marker}",
                ],
                label="activar mantenimiento",
            )
            if package.sql_path is None:
                raise RestoreError("ZIP WordPress sin SQL")
            _restore_database(tooling, target, package.sql_path)
        else:
            mutated = True

        swap = (
            f"mkdir -p -- {q_target} && "
            f"owner=$(stat -c '%u:%g' {q_target}) && "
            f"find {q_target} -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} + && "
            f"cp -a {q_stage}/. {q_target}/ && chown -R \"$owner\" {q_target}"
        )
        _run(
            tooling,
            ["exec", target.container, "sh", "-c", swap],
            label="reemplazar web root",
        )
        if target.kind == "wp":
            marker = shlex.quote(f"{target.site_path.rstrip('/')}/.maintenance")
            _run(
                tooling,
                ["exec", target.container, "sh", "-c", f"rm -f -- {marker}"],
                label="retirar mantenimiento",
            )
    except RestoreError as exc:
        raise ApplyError(str(exc), mutated=mutated) from exc
    finally:
        tooling.docker(
            ["exec", target.container, "sh", "-c", f"rm -rf -- {q_stage}"],
            None,
        )


def create_safety_backup(args: argparse.Namespace, tooling: Tooling) -> Path:
    stamp = datetime.fromtimestamp(tooling.now(), tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    directory = Path(args.safety_out).expanduser() / f"{args.container}-{stamp}-{tooling.token_hex(4)}"
    directory.mkdir(parents=True, mode=0o700)
    os.chmod(directory, 0o700)
    backup_args = argparse.Namespace(
        container=args.container,
        db_container=args.db_container,
        db_host=args.db_host,
        db_user=args.db_user,
        db_pass=args.db_pass,
        db_name=args.db_name,
        path=args.path,
        out=str(directory),
        dry_run=False,
        verify=None,
    )
    try:
        code = backup.run_backup(backup_args, _backup_tooling(tooling))
    except (backup.BackupError, backup.VerifyError) as exc:
        raise RestoreError(f"safety backup falló: {exc}") from exc
    candidates = sorted(directory.glob("*.zip"))
    if code != 0 or len(candidates) != 1:
        raise RestoreError("safety backup no produjo exactamente un ZIP")
    try:
        backup.verify_zip(candidates[0])
    except backup.VerifyError as exc:
        raise RestoreError(f"safety backup inválido: {exc}") from exc
    return candidates[0]


def _plan(package: RestorePackage, target: backup.Target) -> dict:
    return {
        "zip": str(package.zip_path),
        "container": target.container,
        "kind": target.kind,
        "site_path": target.site_path,
        "database": (
            {"container": target.db_container, "name": target.db_name}
            if target.kind == "wp"
            else None
        ),
        "entries": len(package.manifest["entries"]),
        "bytes": package.manifest["total_size"],
    }


def run_restore(args: argparse.Namespace, tooling: Tooling) -> int:
    temp = Path(tempfile.mkdtemp(prefix="site-restore-"))
    preserve_temp = False
    try:
        package = _strict_extract(Path(args.zip_path), temp / "incoming", args.max_bytes)
        target = _target(args, tooling)
        _check_package_target(package, target)
        print(json.dumps(_plan(package, target), indent=2, ensure_ascii=False))
        if not args.yes:
            print("PLAN: sin --yes no se creó safety backup ni se mutó el destino")
            return EXIT_OK

        print("ADVERTENCIA: se reemplazarán archivos" + (" y la DB del sitio" if target.kind == "wp" else ""))
        factory = tooling.safety_backup or create_safety_backup
        safety = factory(args, tooling)
        print(f"SAFETY_BACKUP: {safety}")
        # Probar que el safety ZIP es restaurable ANTES de la primera mutación;
        # verificar solo su checksum no alcanza si luego el rollback no puede
        # extraerlo o no corresponde a este target.
        safety_package = _strict_extract(safety, temp / "safety", args.max_bytes)
        _check_package_target(safety_package, target)
        try:
            _apply(package, target, tooling)
        except ApplyError as exc:
            if not exc.mutated:
                print(f"ERROR: restore falló antes de mutar: {exc}", file=sys.stderr)
                return EXIT_ERROR
            print(f"ERROR: restore falló después de mutar: {exc}; iniciando rollback", file=sys.stderr)
            try:
                _apply(safety_package, target, tooling)
            except (RestoreError, OSError) as rollback_exc:
                preserve_temp = True
                print(
                    f"ROLLBACK FALLÓ: {rollback_exc}; temporales conservados en {temp}",
                    file=sys.stderr,
                )
                return EXIT_ROLLBACK_FAILED
            print("ROLLBACK: estado previo restaurado", file=sys.stderr)
            return EXIT_ERROR

        print("RESTORE: completado")
        return EXIT_OK
    finally:
        if not preserve_temp:
            shutil.rmtree(temp, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Restaura al mismo contenedor un ZIP producido por site_backup.py."
    )
    parser.add_argument("--zip", required=True, dest="zip_path")
    parser.add_argument("--container", required=True)
    parser.add_argument("--db-container", default=None)
    parser.add_argument("--db-host", default=None)
    parser.add_argument("--db-user", default=None)
    parser.add_argument("--db-pass", default=None)
    parser.add_argument("--db-name", default=None)
    parser.add_argument("--path", default=None, help="override del web root actual")
    parser.add_argument("--yes", action="store_true", help="confirma la operación destructiva")
    parser.add_argument(
        "--safety-out",
        default="/root/backups/pre-restore",
        help="raíz donde conservar el backup previo",
    )
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    return parser


def main(argv: list[str] | None = None, tooling: Tooling | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_bytes < 1:
        print("ERROR: --max-bytes debe ser positivo", file=sys.stderr)
        return EXIT_ERROR
    try:
        return run_restore(args, tooling or default_tooling())
    except StrictVerifyError as exc:
        print(f"VERIFY FAIL: {exc}", file=sys.stderr)
        return EXIT_VERIFY
    except RestoreError as exc:
        secret = args.db_pass or ""
        message = str(exc).replace(secret, "***") if secret else str(exc)
        print(f"ERROR: {message}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
