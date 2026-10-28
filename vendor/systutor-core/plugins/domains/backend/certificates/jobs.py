"""Solicitudes y jobs del gestor de certificados.

La web solo escribe `requests/<uuid>.json` (atómico, 0600) y lee `jobs/`.
El bloqueo de archivo evita que dos POST concurrentes creen dos jobs activos
para el mismo hostname (I6).
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from plugins.domains.backend.certificates.config import CertificatesConfig
from plugins.domains.backend.certificates.validation import (
    contains_secret_material,
    normalize_hostname,
    normalize_job_id,
    parse_datetime,
)

try:  # pragma: no cover - Windows no es un objetivo, pero no debe romper imports
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

ACTIVE_STATES = frozenset({"queued", "running"})
JOB_STATES = frozenset({"queued", "running", "succeeded", "failed"})


class ActiveJobError(Exception):
    def __init__(self, job_id: str, hostname: str) -> None:
        super().__init__(f"Ya hay una solicitud activa para {hostname}")
        self.job_id = job_id
        self.hostname = hostname


@dataclass(slots=True)
class JobView:
    job_id: str
    hostname: str
    state: str
    action: str | None = None
    requested_at: str | None = None
    updated_at: str | None = None
    message: str | None = None
    error: str | None = None


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ensure_dir(path: Path, mode: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(mode)
    except OSError:
        # Un directorio del sistema puede no ser nuestro; el archivo se crea
        # igual con el modo explícito.
        pass


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Lock exclusivo por archivo; no-op donde `fcntl` no existe."""

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


def atomic_write_json(path: Path, payload: dict[str, Any], *, mode: int = 0o600) -> None:
    """Escribe JSON completo o nada: mismo directorio, fsync y rename."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
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


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _safe_text(value: object, limit: int = 500) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    if contains_secret_material(value):
        return "Detalle no disponible"
    return value.strip()[:limit]


class JobStore:
    def __init__(self, config: CertificatesConfig) -> None:
        self._config = config

    @property
    def requests_dir(self) -> Path:
        return self._config.requests_dir

    @property
    def jobs_dir(self) -> Path:
        return self._config.jobs_dir

    def _locked(self) -> Iterator[None]:
        return file_lock(self.requests_dir / ".certificate-jobs.lock")

    # ---- escritura -------------------------------------------------------

    def create_request(
        self,
        hostname: str,
        *,
        action: str,
        requested_by: str | None,
        reason: str | None,
    ) -> str:
        normalized = normalize_hostname(hostname)
        with self._locked():
            _ensure_dir(self.requests_dir, 0o770)
            existing = self.active_job(normalized)
            if existing is not None:
                raise ActiveJobError(existing, normalized)
            request_id = str(uuid.uuid4())
            payload = {
                "id": request_id,
                "hostname": normalized,
                "action": action,
                "requested_by": requested_by or "unknown",
                "requested_at": now_iso(),
                "reason": reason or "manual",
            }
            atomic_write_json(self.requests_dir / f"{request_id}.json", payload, mode=0o600)
            return request_id

    def create_refresh(self, *, requested_by: str | None, reason: str | None) -> str:
        with self._locked():
            _ensure_dir(self.requests_dir, 0o770)
            request_id = str(uuid.uuid4())
            payload = {
                "id": request_id,
                "action": "refresh",
                "requested_by": requested_by or "unknown",
                "requested_at": now_iso(),
                "reason": reason or "manual",
            }
            atomic_write_json(
                self.requests_dir / f"refresh-{request_id}.json", payload, mode=0o600
            )
            return request_id

    # ---- lectura ---------------------------------------------------------

    def _requests(self) -> list[tuple[Path, dict[str, Any]]]:
        rows: list[tuple[Path, dict[str, Any]]] = []
        try:
            paths = sorted(self.requests_dir.glob("*.json"))
        except OSError:
            return rows
        for path in paths:
            payload = _read_json(path)
            if payload is not None:
                rows.append((path, payload))
        return rows

    def _jobs(self) -> list[tuple[Path, dict[str, Any]]]:
        rows: list[tuple[Path, dict[str, Any]]] = []
        try:
            paths = sorted(self.jobs_dir.glob("*.json"))
        except OSError:
            return rows
        for path in paths:
            payload = _read_json(path)
            if payload is not None:
                rows.append((path, payload))
        return rows

    def active_job(self, hostname: str) -> str | None:
        needle = hostname.strip().lower()
        terminal: set[str] = set()
        for path, payload in self._jobs():
            if str(payload.get("state", "")).strip().lower() not in ACTIVE_STATES:
                terminal.add(str(payload.get("id") or path.stem))
                continue
            if str(payload.get("hostname", "")).strip().lower() != needle:
                continue
            return str(payload.get("id") or path.stem)
        for path, payload in self._requests():
            if str(payload.get("action", "")).strip().lower() == "refresh":
                continue
            if str(payload.get("hostname", "")).strip().lower() != needle:
                continue
            request_id = str(payload.get("id") or path.stem)
            # El agente escribe el job terminal antes de borrar la solicitud.
            # Esa solicitud ya no está activa.
            if request_id in terminal:
                continue
            return request_id
        return None

    def job(self, hostname: str, job_id: str) -> JobView | None:
        normalized_hostname = normalize_hostname(hostname)
        normalized_id = normalize_job_id(job_id)
        job_path = self.jobs_dir / f"{normalized_id}.json"
        payload = _read_json(job_path)
        if payload is None:
            payload = _read_json(self.requests_dir / f"{normalized_id}.json")
        if payload is None:
            return None
        stored_hostname = str(payload.get("hostname", "")).strip().lower()
        if stored_hostname != normalized_hostname:
            return None
        state = str(payload.get("state", "queued")).strip().lower()
        if state not in JOB_STATES:
            state = "queued" if payload.get("state") is None else "failed"
        requested_at = payload.get("requested_at")
        updated_at = payload.get("finished_at") or payload.get("updated_at") or requested_at
        return JobView(
            job_id=normalized_id,
            hostname=normalized_hostname,
            state=state,
            action=_safe_text(payload.get("action"), 16),
            requested_at=requested_at if isinstance(requested_at, str) else None,
            updated_at=updated_at if isinstance(updated_at, str) else None,
            message=_safe_text(payload.get("message")),
            error=_safe_text(payload.get("error")),
        )

    def pending_count(self) -> int:
        return len(self._requests())

    def state_mtime(self) -> float | None:
        try:
            return self._config.state_path.stat().st_mtime
        except OSError:
            return None


def requested_at_of(payload: dict[str, Any]) -> datetime | None:
    value = payload.get("requested_at")
    return parse_datetime(value) if isinstance(value, str) else None


def read_request(path: Path) -> dict[str, Any] | None:
    return _read_json(path)


def safe_error(value: object) -> str | None:
    return _safe_text(value)


__all__ = [
    "ACTIVE_STATES",
    "ActiveJobError",
    "JOB_STATES",
    "JobStore",
    "JobView",
    "atomic_write_json",
    "file_lock",
    "now_iso",
    "read_request",
    "safe_error",
]
