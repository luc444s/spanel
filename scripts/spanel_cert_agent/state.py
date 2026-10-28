"""Estado y jobs del agente, solo metadatos.

`state.json` nunca contiene el contenido de una clave. Si la serialización
detectara material privado se aborta antes de escribir (I1/I9).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import AgentConfig
from .installer import install_paths
from .validation import (
    CertificateInfo,
    CertificateValidationError,
    contains_secret_material,
    inspect_certificate,
    safe_error,
)

JOB_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TERMINAL_STATES = frozenset({"succeeded", "failed"})

try:  # pragma: no cover
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


class StateError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    if fcntl is None:  # pragma: no cover
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def atomic_write_json(path: Path, payload: dict[str, Any], *, mode: int, lock: bool = True) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if contains_secret_material(serialized):
        raise StateError("Se intentó escribir material privado en un archivo de estado")
    context = file_lock(path.with_name(f".{path.name}.lock")) if lock else _null_lock()
    with context:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(serialized)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, mode)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


@contextmanager
def _null_lock() -> Iterator[None]:
    yield


def ensure_directories(config: AgentConfig) -> None:
    for path, mode in (
        (config.requests_dir, 0o770),
        (config.jobs_dir, 0o750),
        (config.state_path.parent, 0o755),
    ):
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(mode)
        except OSError:
            pass


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def job_id_from(path: Path, payload: dict[str, Any]) -> str:
    candidate = str(payload.get("id") or "").strip().lower()
    if not JOB_ID_RE.match(candidate):
        stem = path.stem.removeprefix("refresh-")
        if not JOB_ID_RE.match(stem):
            raise StateError("Solicitud sin identificador UUID válido")
        candidate = stem
    return candidate


def write_job(config: AgentConfig, job_id: str, payload: dict[str, Any]) -> Path:
    if not JOB_ID_RE.match(job_id):
        raise StateError("Job inválido")
    path = config.jobs_dir / f"{job_id}.json"
    atomic_write_json(path, {"id": job_id, **payload}, mode=0o640)
    return path


def read_job(config: AgentConfig, job_id: str) -> dict[str, Any] | None:
    return read_json(config.jobs_dir / f"{job_id}.json")


def already_finished(config: AgentConfig, job_id: str) -> bool:
    job = read_job(config, job_id)
    return bool(job and str(job.get("state", "")).lower() in TERMINAL_STATES)


def _status(info: CertificateInfo, renew_days: int, now: datetime) -> str:
    if info.not_after is None:
        return "error"
    if info.not_after <= now:
        return "expired"
    if info.not_after <= now + timedelta(days=renew_days):
        return "expiring"
    return "active"


def _previous(config: AgentConfig) -> dict[str, dict[str, Any]]:
    payload = read_json(config.state_path)
    if not payload:
        return {}
    items = payload.get("certificates")
    if not isinstance(items, list):
        return {}
    return {
        str(item.get("hostname")): item
        for item in items
        if isinstance(item, dict) and item.get("hostname")
    }


def _entry(
    hostname: str,
    cert: Path,
    key: Path,
    *,
    deployment: str,
    scope: str,
    domain: str | None,
    source: str,
    now: datetime,
    renew_days: int,
    previous: dict[str, Any] | None,
    issued_at: str | None = None,
    error: str | None = None,
    runner=None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "hostname": hostname,
        "name": hostname,
        "scope": scope,
        "domain": domain,
        "source": source,
        "deployment": deployment,
        "status": "missing",
        "sans": [],
        "issuer": None,
        "subject": None,
        "serial": None,
        "fingerprint": None,
        "not_before": None,
        "not_after": None,
        "cert_path": str(cert),
        "key_path": str(key),
        "last_checked_at": now_iso(),
        "last_issued_at": issued_at or (previous or {}).get("last_issued_at"),
        "last_error": error,
    }
    if not cert.is_file():
        return entry
    kwargs = {"runner": runner} if runner is not None else {}
    try:
        info = inspect_certificate(cert, **kwargs)
    except CertificateValidationError as exc:
        entry["status"] = "error"
        entry["last_error"] = safe_error(str(exc))
        return entry
    entry.update(info.metadata())
    entry["status"] = _status(info, renew_days, now)
    if issued_at:
        entry["last_issued_at"] = issued_at
    return entry


def build_state(
    config: AgentConfig,
    *,
    issued: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    now: datetime | None = None,
    runner=None,
) -> dict[str, Any]:
    reference = now or datetime.now(timezone.utc)
    previous = _previous(config)
    entries: list[dict[str, Any]] = []
    issued = issued or {}
    errors = errors or {}

    if config.default_name:
        cert = config.default_cert_path or Path("/nonexistent")
        key = config.default_key_path or Path("/nonexistent")
        entries.append(
            _entry(
                config.default_name,
                cert,
                key,
                deployment="default",
                scope="global",
                domain=None,
                source=str((previous.get(config.default_name) or {}).get("source") or "static"),
                now=reference,
                renew_days=config.renew_days,
                previous=previous.get(config.default_name),
                error=errors.get(config.default_name),
                runner=runner,
            )
        )

    for hostname in sorted(config.allowlist):
        cert_path, key_path = install_paths(config, hostname)
        entries.append(
            _entry(
                hostname,
                cert_path,
                key_path,
                deployment="sni" if cert_path.is_file() else "none",
                scope="tenant",
                domain=hostname[5:] if hostname.startswith("mail.") else None,
                source=str((previous.get(hostname) or {}).get("source") or "certbot"),
                now=reference,
                renew_days=config.renew_days,
                previous=previous.get(hostname),
                issued_at=issued.get(hostname),
                error=errors.get(hostname),
                runner=runner,
            )
        )

    return {
        "version": 1,
        "generated_at": now_iso(),
        "agent": {"last_seen": now_iso(), "version": "0.1.0", "source": config.source},
        "certificates": entries,
    }


def write_state(config: AgentConfig, state: dict[str, Any]) -> Path:
    ensure_directories(config)
    atomic_write_json(config.state_path, state, mode=0o640)
    return config.state_path


def refresh_state(
    config: AgentConfig,
    *,
    issued: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    runner=None,
) -> dict[str, Any]:
    state = build_state(config, issued=issued, errors=errors, runner=runner)
    write_state(config, state)
    return state


def new_job_payload(
    *,
    hostname: str | None,
    action: str,
    state: str,
    requested_by: str | None = None,
    requested_at: str | None = None,
    message: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    stamp = now_iso()
    payload: dict[str, Any] = {
        "hostname": hostname,
        "action": action,
        "state": state,
        "requested_by": requested_by,
        "requested_at": requested_at or stamp,
        "updated_at": stamp,
        "message": message,
        "error": error,
    }
    if state in TERMINAL_STATES:
        payload["finished_at"] = stamp
    return payload


def uuid4() -> str:
    return str(uuid.uuid4())
