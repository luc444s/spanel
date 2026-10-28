"""Ejecución `run-once` del agente.

Flujo: refresca el estado, procesa cada solicitud pendiente, escribe el job y
borra la solicitud. Un fallo deja el job en `failed` y **no** modifica el
estado ni toca certificados activos (I10).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .certbot import CertbotError, build_argv, lineage_dir
from .config import AgentConfig, AgentConfigError
from .installer import InstallError, install_pair, install_paths
from .operations import install_current, rollback_current, run_audit, sync_hostname
from .sni import SniError, apply_sni
from .state import (
    StateError,
    already_finished,
    build_state,
    ensure_directories,
    job_id_from,
    new_job_payload,
    now_iso,
    read_json,
    refresh_state,
    write_job,
    write_state,
)
from .validation import (
    CertificateValidationError,
    safe_error,
    validate_request_payload,
)

CommandRunner = Callable[[Sequence[str], bytes | None], subprocess.CompletedProcess]


def _run_command(argv: Sequence[str], input_data: bytes | None = None):
    return subprocess.run(list(argv), input=input_data, capture_output=True, check=False)


@dataclass(slots=True)
class RunResult:
    processed: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"processed": self.processed, "failed": self.failed}


class CertAgent:
    def __init__(
        self,
        config: AgentConfig,
        *,
        runner: CommandRunner = _run_command,
        connector=None,
        verifier=None,
        alert_sender=None,
    ) -> None:
        self.config = config
        self._runner = runner
        self._connector = connector
        self._verifier = verifier
        self._alert_sender = alert_sender

    # ---- estado ----------------------------------------------------------

    def refresh(self) -> dict:
        ensure_directories(self.config)
        return refresh_state(self.config, runner=self._runner)

    def _installed_entries(self) -> dict[str, tuple[Path, Path]]:
        entries: dict[str, tuple[Path, Path]] = {}
        for hostname in sorted(self.config.allowlist):
            cert, key = install_paths(self.config, hostname)
            if cert.is_file() and key.is_file():
                entries[hostname] = (cert, key)
        return entries

    # ---- solicitudes -----------------------------------------------------

    def run_once(self) -> RunResult:
        ensure_directories(self.config)
        # Un timer sin solicitudes también mantiene el estado fresco.
        refresh_state(self.config, runner=self._runner)
        result = RunResult()
        for path in sorted(self.config.requests_dir.glob("*.json")):
            try:
                outcome = self.process_request(path)
            except Exception:  # noqa: BLE001 - una solicitud no debe tumbar el lote
                outcome = False
            result.processed += 1
            if not outcome:
                result.failed += 1
        return result

    def process_request(self, path: Path) -> bool:
        payload = read_json(path)
        if payload is None:
            self._discard(path)
            return False
        try:
            job_id = job_id_from(path, payload)
        except Exception:  # noqa: BLE001 - una solicitud corrupta no debe romper el lote
            self._discard(path)
            return False
        if already_finished(self.config, job_id):
            self._discard(path)
            return True

        requested_at = payload.get("requested_at")
        raw_hostname = payload.get("hostname")
        if not isinstance(raw_hostname, str):
            raw_hostname = None

        try:
            request = validate_request_payload(
                payload, default_name=self.config.default_name
            )
        except CertificateValidationError as exc:
            self._fail(
                job_id,
                hostname=raw_hostname,
                action=str(payload.get("action") or "unknown"),
                requested_at=requested_at if isinstance(requested_at, str) else None,
                error=str(exc),
            )
            self._discard(path)
            return False

        if request["action"] == "refresh":
            return self._refresh_job(job_id, requested_at, path)

        hostname = request["hostname"]
        self._job(
            job_id,
            new_job_payload(
                hostname=hostname,
                action=request["action"],
                state="running",
                requested_by=request.get("requested_by"),
                requested_at=requested_at if isinstance(requested_at, str) else None,
            ),
        )
        try:
            self.config.require_hostname(hostname)
            # Antes de emitir: si falta SNI o recarga, se falla cerrado.
            self.config.require_reload_config()
            if request["action"] in ("verify", "sync", "install", "rollback"):
                return self._run_health_action(
                    request["action"],
                    hostname,
                    job_id,
                    requested_at,
                    path,
                    requested_by=request.get("requested_by"),
                )
            argv = build_argv(self.config, hostname, request["action"])
            process = self._runner(argv, None)
            if process.returncode != 0:
                detail = (process.stderr or process.stdout or b"").decode(
                    "utf-8", "replace"
                )
                raise CertbotError(safe_error(detail) or "Certbot falló")

            cert_path, key_path, _info = install_pair(
                self.config,
                hostname,
                lineage_dir(self.config, hostname),
                runner=self._runner,
            )
            entries = self._installed_entries()
            entries[hostname] = (cert_path, key_path)
            apply_sni(self.config, entries, runner=self._runner)
            refresh_state(
                self.config,
                issued={hostname: now_iso()},
                runner=self._runner,
            )
            self._job(
                job_id,
                new_job_payload(
                    hostname=hostname,
                    action=request["action"],
                    state="succeeded",
                    requested_by=request.get("requested_by"),
                    requested_at=requested_at if isinstance(requested_at, str) else None,
                    message="Certificado instalado",
                ),
            )
            self._discard(path)
            return True
        except (
            AgentConfigError,
            CertbotError,
            CertificateValidationError,
            InstallError,
            SniError,
            StateError,
            OSError,
        ) as exc:
            self._fail(
                job_id,
                hostname=hostname,
                action=request["action"],
                requested_at=requested_at if isinstance(requested_at, str) else None,
                error=str(exc),
                requested_by=request.get("requested_by"),
            )
            self._discard(path)
            return False

    def _run_health_action(
        self,
        action: str,
        hostname: str,
        job_id: str,
        requested_at,
        path: Path,
        *,
        requested_by: str | None,
    ) -> bool:
        """Verificación/instalación/rollback; nunca ejecuta Certbot (I4)."""

        if action == "verify":
            run_audit(
                self.config,
                connector=self._connector,
                runner=self._runner,
                sender=self._alert_sender,
            )
            ok = True
            message = "Verificación completada"
            error = None
        else:
            runner = self._runner
            if action == "sync":
                outcome = sync_hostname(
                    self.config,
                    hostname,
                    runner=runner,
                    verifier=self._verifier,
                    connector=self._connector,
                    sender=self._alert_sender,
                )
            elif action == "install":
                outcome = install_current(
                    self.config,
                    hostname,
                    runner=runner,
                    verifier=self._verifier,
                    connector=self._connector,
                    sender=self._alert_sender,
                )
            else:
                outcome = rollback_current(
                    self.config,
                    hostname,
                    runner=runner,
                    verifier=self._verifier,
                    connector=self._connector,
                    sender=self._alert_sender,
                )
            ok = outcome.ok
            message = "Certificado instalado" if ok else None
            error = outcome.error
        self._job(
            job_id,
            new_job_payload(
                hostname=hostname,
                action=action,
                state="succeeded" if ok else "failed",
                requested_by=requested_by,
                requested_at=requested_at if isinstance(requested_at, str) else None,
                message=message,
                error=None if ok else (error or "Operación fallida"),
            ),
        )
        self._discard(path)
        return ok

    def _refresh_job(self, job_id: str, requested_at, path: Path) -> bool:
        self._job(
            job_id,
            new_job_payload(
                hostname=None,
                action="refresh",
                state="running",
                requested_at=requested_at if isinstance(requested_at, str) else None,
            ),
        )
        try:
            state = build_state(self.config, runner=self._runner)
            write_state(self.config, state)
        except (StateError, OSError) as exc:
            self._fail(
                job_id,
                hostname=None,
                action="refresh",
                requested_at=requested_at if isinstance(requested_at, str) else None,
                error=str(exc),
            )
            self._discard(path)
            return False
        self._job(
            job_id,
            new_job_payload(
                hostname=None,
                action="refresh",
                state="succeeded",
                requested_at=requested_at if isinstance(requested_at, str) else None,
                message="Estado actualizado",
            ),
        )
        self._discard(path)
        return True

    # ---- utilidades ------------------------------------------------------

    def _job(self, job_id: str, payload: dict) -> None:
        try:
            write_job(self.config, job_id, payload)
        except Exception:  # noqa: BLE001, S110 - no romper el lote por un job ilegible
            pass

    def _fail(
        self,
        job_id: str,
        *,
        hostname: str | None,
        action: str,
        requested_at: str | None,
        error: str,
        requested_by: str | None = None,
    ) -> None:
        self._job(
            job_id,
            new_job_payload(
                hostname=hostname,
                action=action,
                state="failed",
                requested_by=requested_by,
                requested_at=requested_at,
                error=safe_error(error) or "Error desconocido",
            ),
        )

    @staticmethod
    def _discard(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass
