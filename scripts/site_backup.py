#!/usr/bin/env python3
"""site_backup.py — respaldo completo de un sitio en un .zip (A.SPEC 0047).

WordPress (archivos + DB) y estáticos (solo archivos) en un solo script.
Detección automática: si `docker inspect` del container expone `WORDPRESS_DB_*`
→ respaldo WP; si no → estático. Acciones read-only para el container (I1):
inspect → `docker cp <c>:<path> -` (tar a tmp) → (WP) `docker exec <db> sh -c
'exec mysqldump ...'` → zip `<out>/<kind>-<container>-<ts>.zip` (manifest.json
con SHA256 por entry, site/**, database/backup.sql) con build en tmp +
os.replace atómico (I2) y self-verify de hashes (I3). El manifest jamás guarda
credenciales (I4). Exit codes: 0 ok, 1 error de ejecución, 2 verificación.

Uso: python3 site_backup.py --container legalattorney-clean \
         --db-container legalattorney-clean-db
     python3 site_backup.py --container portafolio
     python3 site_backup.py --verify backups/wp-sitio-20260101-000000.zip
stdlib only, sin pip (I5).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_RUN_ERROR = 1
EXIT_VERIFY_ERROR = 2

DEFAULT_OUT = "/root/backups"
WP_SITE_PATH = "/var/www/html"
FALLBACK_SITE_PATH = "/usr/share/nginx/html"

# spanel.profile → web_root del catálogo default (caddy/httpd alias de react/boot).
PROFILE_CATALOG = {
    "nginx": "/usr/share/nginx/html",
    "plain": "/usr/share/nginx/html",  # alias del prototipo de lab (0042)
    "caddy": "/usr/share/caddy",
    "react": "/usr/share/caddy",
    "astro": "/site",
    "httpd": "/usr/local/apache2/htdocs",
    "boot": "/usr/local/apache2/htdocs",
}

WP_ENV_KEYS = ("WORDPRESS_DB_HOST", "WORDPRESS_DB_USER", "WORDPRESS_DB_PASSWORD", "WORDPRESS_DB_NAME")


class BackupError(Exception):
    """Error de ejecución → exit 1."""


class VerifyError(Exception):
    """Error de verificación → exit 2."""


def _argv_text(argv: list[str]) -> list[str]:
    """El argv del dump puede llevar el password; se enmascara en stderr (I4)."""
    out = []
    for token in argv:
        if len(token) > 256:  # el comando mysqldump en sh -c cruza este size
            out.append(token[:24] + "...<redactado>")
        else:
            out.append(token)
    return out


@dataclass(frozen=True)
class Result:
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    stdout_bytes: bytes | None = None  # para el mysqldump sin transcodificación


def docker_cli(args: list[str]) -> Result:
    """Docker CLI real (respeta DOCKER_HOST). I1: solo inspect/cp/exec-mysqldump.

    Captura SIN text=True: el tar del `docker cp -` y el mysqldump son
    binarios (los dumps llevan bytes latin-1) y el decode automático
    UnicodeDecodeError-iza (bug real del lab). stdout lleva un preview UTF-8
    con replacement; stdout_bytes trae los bytes exactos para cp/DB."""
    try:
        proc = subprocess.run(
            ["docker", *args],
            capture_output=True, timeout=600, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Result("", f"docker CLI indisponible: {exc}", 127)
    return Result(
        stdout=proc.stdout[:16_384].decode("utf-8", errors="replace"),
        stderr=proc.stderr.decode("utf-8", errors="replace"),
        returncode=proc.returncode,
        stdout_bytes=proc.stdout,
    )


@dataclass
class Tooling:
    """Puntos de inyección para tests: docker CLI y reloj."""
    docker: Callable[[list[str]], Result] = docker_cli
    # El dump viaja en bytes sin transcodificación; los tests inyectan también.
    docker_bytes: Callable[[list[str]], Result] = docker_cli
    now: Callable[[], float] = time.time


# --- Inspección y detección --------------------------------------------------- #


@dataclass
class Target:
    container: str
    kind: str  # "wp" | "static"
    site_path: str
    env: dict[str, str] = field(default_factory=dict)
    db_host: str | None = None
    db_container: str | None = None
    db_user: str | None = None
    db_name: str | None = None
    db_pass: str | None = None  # en memoria solo; jamás al manifest/zip (I4)


def inspect_container(tooling: Tooling, container: str) -> dict:
    out = tooling.docker(["inspect", container])
    if out.returncode != 0:
        raise BackupError(f"docker inspect {container} falló: {out.stderr.strip() or out.stdout.strip()}")
    try:
        return json.loads(out.stdout)[0]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
        raise BackupError(f"docker inspect {container} devolvió JSON inválido: {exc}") from exc


def env_of(inspect_data: dict) -> dict[str, str]:
    return dict(
        entry.partition("=")[::2]
        for entry in inspect_data.get("Config", {}).get("Env") or []
    )

def resolve_site_path(kind: str, labels: dict[str, str], env: dict[str, str], override: str | None) -> str:
    """--path override > WP fijo > label spanel.profile → catálogo > fallback."""
    if override:
        return override
    if kind == "wp":
        return WP_SITE_PATH
    profile = (labels.get("spanel.profile") or "").strip().lower()
    if profile and profile not in PROFILE_CATALOG:
        known = ", ".join(sorted(PROFILE_CATALOG))
        raise BackupError(f"label spanel.profile={profile!r} no está en el catálogo default ({known}); usar --path")
    return PROFILE_CATALOG.get(profile, FALLBACK_SITE_PATH)


def db_container_from_host(host: str | None) -> str | None:
    """WORDPRESS_DB_HOST is `name` or `name:port`; docker exec needs `name`."""
    name = (host or "").split(":", 1)[0].strip()
    return name or None


def build_target(
    tooling: Tooling, container: str, args: argparse.Namespace
) -> Target:
    inspect_data = inspect_container(tooling, container)
    env = env_of(inspect_data)
    labels = dict(inspect_data.get("Config", {}).get("Labels") or {})

    detected = "wp" if any(key in env for key in WP_ENV_KEYS) else "static"
    kind = detected if args.kind == "auto" else args.kind

    if kind == "wp":
        db_host = args.db_host or env.get("WORDPRESS_DB_HOST")
        db_user = args.db_user or env.get("WORDPRESS_DB_USER")
        db_name = args.db_name or env.get("WORDPRESS_DB_NAME")
        db_pass = (
            args.db_pass
            or os.getenv("SPANEL_BACKUP_DB_PASSWORD")
            or env.get("WORDPRESS_DB_PASSWORD")
        )
        fields = {
            "db-host": db_host,
            "db-user": db_user,
            "db-name": db_name,
            "db-pass (env, SPANEL_BACKUP_DB_PASSWORD o --db-pass)": db_pass,
            "db-container": args.db_container or db_container_from_host(db_host),
        }
        missing = [name for name, value in fields.items() if not value]
        if missing:
            raise BackupError(
                "container WP sin base de datos completa; faltan: "
                + ", ".join(sorted(missing))
                + " — usar --db-host/--db-user/--db-pass/--db-name/--db-container"
            )
        return Target(
            container=container,
            kind="wp",
            site_path=resolve_site_path("wp", labels, env, args.path),
            env={k: v for k, v in env.items() if k != "WORDPRESS_DB_PASSWORD"},
            db_host=db_host,
            db_container=args.db_container or db_container_from_host(db_host),
            db_user=db_user,
            db_name=db_name,
            db_pass=db_pass,
        )

    return Target(
        container=container,
        kind="static",
        site_path=resolve_site_path("static", labels, env, args.path),
    )


# --- Captura: files (docker cp → tar) y DB (mysqldump, I1) -------------------- #


def extract_tar_stream(result: Result, dest: Path) -> int:
    """Extrae el tar de `docker cp container:path -` a dest; streaming a file
    temporal primero (memory-safe). Devuelve N archivos extraídos."""
    if result.returncode != 0:
        raise BackupError(f"docker cp falló: {result.stderr.strip() or result.stdout.strip()}")
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        raw = result.stdout_bytes if result.stdout_bytes is not None else result.stdout.encode("utf-8")
        tmp.write(raw)
    try:
        extracted = 0
        with tarfile.open(tmp_path, "r:") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    tar.extract(member, dest, filter="data")
                    extracted += 1
        return extracted
    except tarfile.TarError as exc:
        raise BackupError(f"docker cp no devolvió un tar válido: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


def capture_files(tooling: Tooling, target: Target, tmp: Path) -> int:
    print(f"[cp] {target.container}:{target.site_path} -> site/")
    result = tooling.docker(["cp", f"{target.container}:{target.site_path}", "-"])
    n = extract_tar_stream(result, tmp / "site")
    print(f"[cp] {n} archivos capturados")
    return n


def dump_db(tooling: Tooling, target: Target, tmp: Path) -> None:
    """mysqldump vía `docker exec`; falla antes de zip si falla o queda vacío (I2)."""
    if not target.db_container:
        raise BackupError(
            "no se pudo resolver el container de DB (WORDPRESS_DB_HOST no es un "
            "container corriendo?); usar --db-container"
        )
    password = target.db_pass or ""
    # Multi-tenant: la DB compartida (p.ej. un mariadb global con N bases)
    # se respeta siempre con un dump de UNA base — la del sitio
    # (WORDPRESS_DB_NAME). Jamás --all-databases/--databases con comodines:
    # el volcado no debe tomar el resto de los tenants dentro de la multi-DB.
    base = (
        f"exec mysqldump -u{target.db_user} -p{password} "
        f"--no-tablespaces {target.db_name}"
    )
    # mariadb:11+ no trae mysqldump en PATH (solo mariadb-dump); fallback.
    candidates = [base] + [
        base.replace("exec mysqldump", f"exec {alt}")
        for alt in ("mariadb-dump",)
    ]
    print(f"[db] mysqldump {target.db_host}/{target.db_name} via {target.db_container}")
    result = None
    last_detail = ""
    for command in candidates:
        result = tooling.docker_bytes(["exec", target.db_container, "sh", "-c", command])
        sql_bytes = (
            result.stdout_bytes
            if result.stdout_bytes is not None
            else result.stdout.encode("utf-8")
        )
        if result.returncode == 0:
            break
        last_detail = (result.stderr.strip() or "sin stderr").replace(password, "***")
        if "not found" not in last_detail.lower():
            break
    sql_bytes = (
        result.stdout_bytes
        if result.stdout_bytes is not None
        else result.stdout.encode("utf-8")
    )
    if result is None or result.returncode != 0:
        raise BackupError(f"mysqldump falló en {target.db_container}: {last_detail}")
    if not sql_bytes.strip():
        raise BackupError("mysqldump produjo SQL vacío — fallando antes del zip (I2)")
    dest = tmp / "database" / "backup.sql"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(sql_bytes)
    print(f"[db] {len(sql_bytes)} bytes SQL capturados (binario-loyal)")


# --- Empaquetado + self-verify (I2/I3) ---------------------------------------- #


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_tree(base: Path) -> list[Path]:
    return sorted(p for p in base.rglob("*") if p.is_file())


def build_manifest(kind: str, container: str, timestamp: str, db_host: str | None, site_dir: Path, sql_path: Path | None) -> dict:
    entries: dict[str, dict[str, int | str]] = {}
    for path in collect_tree(site_dir):
        entries["site/" + path.relative_to(site_dir).as_posix()] = {
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
    if sql_path is not None:
        if not sql_path.is_file():
            raise VerifyError("database/backup.sql no existe tras el dump (invariante I3)")
        entries["database/backup.sql"] = {"sha256": sha256_file(sql_path), "size": sql_path.stat().st_size}
    return {
        "kind": kind,
        "container": container,
        "timestamp": timestamp,
        "db_host": f"db:{db_host}" if (kind == "wp" and db_host) else None,
        "entries": entries,
        "total_size": sum(int(e["size"]) for e in entries.values()),
    }


def build_zip(kind: str, container: str, timestamp: str, out_dir: Path, tmp: Path, db_host: str | None) -> Path:
    """Arma manifest + zip en el tmp, luego os.replace al path final (I2)."""
    site_dir = tmp / "site"
    sql_path = tmp / "database" / "backup.sql" if kind == "wp" else None
    if not any(collect_tree(site_dir)):
        raise BackupError("docker cp no trajo archivos de site/")
    manifest = build_manifest(kind, container, timestamp, db_host, site_dir, sql_path)
    final_path = out_dir / f"{kind}-{container}-{timestamp}.zip"
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_tmp = tmp / ".build.zip"
    with zipfile.ZipFile(zip_tmp, "w", zipfile.ZIP_DEFLATED) as zipped:
        for path in collect_tree(site_dir):
            zipped.write(path, "site/" + path.relative_to(site_dir).as_posix())
        if sql_path is not None and sql_path.is_file():
            zipped.write(sql_path, "database/backup.sql")
        zipped.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    verify_zip(zip_tmp)  # self-verify pre-publicación (I3)
    os.replace(zip_tmp, final_path)  # atómico: completo o no existe (I2)
    return final_path


def verify_zip(zip_path: Path) -> dict:
    """Reabre el zip y verifica manifest, SHA256/size por entry y contenido
    mínimo (site/ + SQL para WP). Raise VerifyError → exit 2 (I3)."""
    if not zip_path.is_file():
        raise VerifyError(f"no existe el zip: {zip_path}")
    try:
        with zipfile.ZipFile(zip_path) as zipped:
            if "manifest.json" not in zipped.namelist():
                raise VerifyError(f"zip sin manifest.json: {zip_path}")
            manifest = json.loads(zipped.read("manifest.json").decode("utf-8"))
            entries = manifest.get("entries") or {}
            if not entries:
                raise VerifyError("manifest sin entries")
            present = set(zipped.namelist())
            for arcname, meta in sorted(entries.items()):
                if arcname not in present:
                    raise VerifyError(f"entry faltante en zip: {arcname}")
                info = zipped.getinfo(arcname)
                if hashlib.sha256(zipped.read(arcname)).hexdigest() != meta.get("sha256"):
                    raise VerifyError(f"SHA256 mismatch en {arcname}")
                meta_size = int(meta.get("size", -1))
                if meta_size != info.file_size:
                    raise VerifyError(f"size mismatch: {arcname} ({info.file_size} vs {meta_size})")
            kind = manifest.get("kind")
            if kind not in ("wp", "static"):
                raise VerifyError(f"kind inválido en manifest: {kind!r}")
            if kind == "wp" and "database/backup.sql" not in present:
                raise VerifyError("WP sin database/backup.sql")
            if not present & {name for name in present if name.startswith("site/")}:
                raise VerifyError("zip sin archivos site/")
        return manifest
    except zipfile.BadZipFile as exc:
        raise VerifyError(f"zip corrupto: {zip_path} ({exc})") from exc


# --- Orquestación y CLI -------------------------------------------------------- #


def run_backup(args: argparse.Namespace, tooling: Tooling) -> int:
    target = build_target(tooling, args.container, args)
    timestamp = datetime.fromtimestamp(tooling.now(), tz=timezone.utc).strftime("%Y%m%d-%H%M%S")

    if args.dry_run:
        plan = {
            "container": target.container,
            "kind": target.kind,
            "site_path": target.site_path,
            "db": {"container": target.db_container, "name": target.db_name} if target.kind == "wp" else None,
            "out": args.out,
            "zip": f"{target.kind}-{target.container}-{timestamp}.zip",
        }
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        print("dry-run: nada escrito (I2)")
        return EXIT_OK

    out_dir = Path(args.out)
    tmp = Path(tempfile.mkdtemp(prefix="site-backup-"))
    try:
        capture_files(tooling, target, tmp)
        if target.kind == "wp":
            dump_db(tooling, target, tmp)
        final = build_zip(target.kind, target.container, timestamp, out_dir, tmp, target.db_host)
        manifest = verify_zip(final)
        entries = manifest.get("entries") or {}
        mb = manifest.get("total_size", 0) / (1024 * 1024)
        print(f"[zip] {final} ({len(entries)} entries, {mb:.2f} MB) — verify OK")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="site_backup.py",
        description="Respaldo completo de un sitio (WP o estático) en un .zip verificable (A.SPEC 0047).",
    )
    parser.add_argument("--container", default=None, help="container del sitio a respaldar")
    parser.add_argument("--db-container", default=None, help="override del container de DB (WP)")
    parser.add_argument("--db-host", default=None, help="override del host de DB")
    parser.add_argument("--db-user", default=None, help="override del usuario de DB")
    parser.add_argument("--db-pass", default=None, help="override del password de DB (no persiste, I4)")
    parser.add_argument("--db-name", default=None, help="override del nombre de la DB")
    parser.add_argument("--path", default=None, help="override del site path dentro del container")
    parser.add_argument(
        "--kind",
        choices=("auto", "wp", "static"),
        default="auto",
        help="forzar el tipo de sitio (auto detecta por WORDPRESS_DB_*)",
    )
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"dir de salida (default {DEFAULT_OUT})")
    parser.add_argument("--dry-run", action="store_true", help="printear el plan sin escribir")
    parser.add_argument("--verify", default=None, metavar="ZIP", help="re-verificar un.zip previo (exit 0/2)")
    return parser


def main(argv: list[str] | None = None, tooling: Tooling | None = None) -> int:
    tooling = tooling or Tooling()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.verify:
        try:
            manifest = verify_zip(Path(args.verify))
        except VerifyError as exc:
            print(f"VERIFY FAIL: {exc}", file=sys.stderr)
            return EXIT_VERIFY_ERROR
        print(f"VERIFY OK: {args.verify} ({len(manifest.get('entries') or {})} entries)")
        return EXIT_OK

    if not args.container:
        parser.error("--container es obligatorio (o --verify ZIP)")

    try:
        return run_backup(args, tooling)
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    except VerifyError as exc:
        print(f"VERIFY FAIL: {exc}", file=sys.stderr)
        return EXIT_VERIFY_ERROR


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
