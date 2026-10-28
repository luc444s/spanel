"""Instalación transaccional con rollback y generaciones.

Orden de la A.SPEC 0036: lock por hostname, validación, staging, backup de
generación/mapa/snippet/health, activación atómica, recarga, verificación de
465/587 y 993, verificación de todos los SNI y sin SNI, commit. Cualquier fallo
entre activación y verificación restaura ambos demonios (I3). Los fallos de
commit (poda/métricas) no revierten el certificado activo (I9).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import AgentConfig, AgentConfigError
from .health import now_iso
from .installer import _atomic_copy, install_paths
from .probe import verify_hostname
from .sni import (
    SniError,
    _run,
    render_dovecot_local_name,
    render_postfix_map,
)
from .state import read_json, try_file_lock
from .validation import (
    CertificateValidationError,
    safe_error,
    validate_pair,
)

Verifier = Callable[[str], bool]
KEEP_GENERATIONS = 3


class InstallBusy(Exception):
    """Otra ejecución tiene el lock del hostname: sale sin cambios."""


class InstallError(Exception):
    """La instalación no pudo completarse; el estado activo ya fue restaurado."""


ACTIVATION_ERRORS = (
    AgentConfigError,
    CertificateValidationError,
    InstallError,
    SniError,
    OSError,
)


@dataclass(slots=True)
class InstallOutcome:
    hostname: str
    ok: bool
    changed: bool
    generation: str | None = None
    previous_generation: str | None = None
    rolled_back: bool = False
    error: str | None = None
    warnings: list[str] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "hostname": self.hostname,
            "ok": self.ok,
            "changed": self.changed,
            "generation": self.generation,
            "previous_generation": self.previous_generation,
            "rolled_back": self.rolled_back,
            "error": self.error,
            "warnings": list(self.warnings or []),
        }


# ---- generaciones -------------------------------------------------------


def generation_root(config: AgentConfig, hostname: str) -> Path:
    return config.generations_root / hostname


def _active_link(config: AgentConfig, hostname: str) -> Path:
    return generation_root(config, hostname) / "active"


def current_generation(config: AgentConfig, hostname: str) -> str | None:
    link = _active_link(config, hostname)
    try:
        target = os.readlink(link)
    except OSError:
        return None
    name = Path(target).name
    return name or None


def _generations(config: AgentConfig, hostname: str) -> list[str]:
    root = generation_root(config, hostname)
    try:
        return sorted(
            child.name
            for child in root.iterdir()
            if child.is_dir() and child.name.startswith("gen-")
        )
    except OSError:
        return []


def previous_generation(config: AgentConfig, hostname: str) -> str | None:
    active = current_generation(config, hostname)
    candidates = [name for name in _generations(config, hostname) if name != active]
    return candidates[-1] if candidates else None


def _new_generation_name() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"gen-{stamp}-{uuid.uuid4().hex[:8]}"


def create_generation(config: AgentConfig, hostname: str, lineage: Path, *, runner) -> str:
    """Copia y valida el material en una generación staging (sin activar)."""

    cert_source = lineage / "fullchain.pem"
    key_source = lineage / "privkey.pem"
    if not cert_source.is_file() or not key_source.is_file():
        raise InstallError(f"El linaje de Certbot está incompleto: {lineage}")
    validate_pair(
        cert_source,
        key_source,
        hostname,
        ca_file=config.verification.ca_file,
        runner=runner,
    )
    name = _new_generation_name()
    directory = generation_root(config, hostname) / name
    directory.mkdir(parents=True, exist_ok=False)
    try:
        _atomic_copy(cert_source, directory / "fullchain.pem", 0o644)
        _atomic_copy(key_source, directory / "privkey.pem", 0o600)
        validate_pair(
            directory / "fullchain.pem",
            directory / "privkey.pem",
            hostname,
            ca_file=config.verification.ca_file,
            runner=runner,
        )
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return name


def activate_generation(config: AgentConfig, hostname: str, name: str) -> None:
    """Activa por symlink atómico y copia el par a las rutas estables."""

    directory = generation_root(config, hostname) / name
    if not (directory / "fullchain.pem").is_file():
        raise InstallError(f"Generación incompleta: {name}")
    link = _active_link(config, hostname)
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".active.{uuid.uuid4().hex}")
    os.symlink(name, temporary)
    os.replace(temporary, link)
    cert_path, key_path = install_paths(config, hostname)
    _atomic_copy(directory / "fullchain.pem", cert_path, 0o644)
    _atomic_copy(directory / "privkey.pem", key_path, 0o600)


def _remove_active_install(config: AgentConfig, hostname: str) -> None:
    cert_path, key_path = install_paths(config, hostname)
    for path in (cert_path, key_path):
        try:
            path.unlink()
        except OSError:
            pass


# ---- backup / restauración ---------------------------------------------


@dataclass(slots=True)
class Backup:
    directory: Path
    previous_generation: str | None
    postfix_path: Path | None
    dovecot_path: Path | None


def _atomic_text(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
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


def _copy_if_exists(source: Path | None, destination: Path) -> bool:
    if source is None or not source.is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def create_backup(
    config: AgentConfig,
    hostname: str,
    *,
    job_id: str,
    previous_state: dict | None = None,
) -> Backup:
    directory = config.backup_root / hostname / job_id
    directory.mkdir(parents=True, exist_ok=True)
    postfix_path = config.postfix.sni_map_path
    dovecot_path = config.dovecot.local_name_path
    has_postfix = _copy_if_exists(postfix_path, directory / "postfix.sni")
    has_dovecot = _copy_if_exists(dovecot_path, directory / "dovecot.local_name")
    previous = current_generation(config, hostname)
    previous_health = {}
    if isinstance(previous_state, dict):
        health = previous_state.get("health")
        if isinstance(health, dict):
            previous_health = health
    meta = {
        "hostname": hostname,
        "created_at": now_iso(),
        "previous_generation": previous,
        "has_postfix_map": has_postfix,
        "has_dovecot_snippet": has_dovecot,
        "health": previous_health,
    }
    _atomic_text(directory / "meta.json", json.dumps(meta, sort_keys=True) + "\n", mode=0o640)
    return Backup(
        directory=directory,
        previous_generation=previous,
        postfix_path=postfix_path,
        dovecot_path=dovecot_path,
    )


def restore_backup(config: AgentConfig, hostname: str, backup: Backup) -> None:
    if backup.previous_generation:
        activate_generation(config, hostname, backup.previous_generation)
    else:
        _remove_active_install(config, hostname)
        try:
            _active_link(config, hostname).unlink()
        except OSError:
            pass
    saved_postfix = backup.directory / "postfix.sni"
    if backup.postfix_path is not None and saved_postfix.is_file():
        _atomic_text(backup.postfix_path, saved_postfix.read_text(encoding="utf-8"))
    saved_dovecot = backup.directory / "dovecot.local_name"
    if backup.dovecot_path is not None and saved_dovecot.is_file():
        _atomic_text(backup.dovecot_path, saved_dovecot.read_text(encoding="utf-8"))


# ---- configuración y verificación --------------------------------------


def installed_entries(config: AgentConfig) -> dict[str, tuple[Path, Path]]:
    entries: dict[str, tuple[Path, Path]] = {}
    for hostname in sorted(config.allowlist):
        cert, key = install_paths(config, hostname)
        if cert.is_file() and key.is_file():
            entries[hostname] = (cert, key)
    return entries


def write_sni_files(config: AgentConfig, entries: Mapping[str, tuple[Path, Path]]) -> None:
    config.require_reload_config()
    assert config.postfix.sni_map_path is not None
    assert config.dovecot.local_name_path is not None
    _atomic_text(config.postfix.sni_map_path, render_postfix_map(entries))
    _atomic_text(config.dovecot.local_name_path, render_dovecot_local_name(entries))


def default_verifier(
    config: AgentConfig,
    hostname: str,
    *,
    connector=None,
) -> Verifier:
    """Probes reales por etapa; el instalador puede inyectar uno falso en tests."""

    def default_serial() -> str | None:
        # La fuente de verdad es el archivo del predeterminado; state.json es
        # solo el respaldo cuando el archivo no está disponible.
        if config.default_cert_path is not None:
            serial = _serial_of(config.default_cert_path)
            if serial:
                return serial
        state = read_json(config.state_path) or {}
        raw_certificates = state.get("certificates")
        if isinstance(raw_certificates, list):
            for entry in raw_certificates:
                if isinstance(entry, dict) and entry.get("hostname") == config.default_name:
                    return entry.get("serial")
        return None

    def selected_ports(stage: str) -> tuple[int, ...]:
        if stage == "dovecot":
            return tuple(port for port in config.verification.ports if port == 993)
        if stage == "postfix":
            return tuple(port for port in config.verification.ports if port != 993)
        return config.verification.ports

    def check(stage: str) -> bool:
        ports = selected_ports(stage)
        if not ports:
            return True
        # Los seriales se recalculan en cada comprobación: tras activar, las
        # rutas estables ya apuntan al material nuevo (si se capturaran una vez,
        # toda activación se revertiría por serial desactualizado).
        entries = installed_entries(config)
        serials = {target: _serial_of(cert) for target, (cert, _key) in entries.items()}
        no_sni_serial = default_serial()
        for target in (hostname, *[name for name in entries if name != hostname]):
            result = verify_hostname(
                target,
                expected_serial=serials.get(target),
                ca_file=config.verification.ca_file,
                ports=ports,
                timeout=config.verification.timeout_seconds,
                no_sni_expected_serial=no_sni_serial,
                connector=connector,
            )
            if not result["sni_ok"] or not result["no_sni_ok"]:
                return False
        return True

    return check


def _serial_of(cert: Path) -> str | None:
    from .validation import inspect_certificate

    try:
        return inspect_certificate(cert).serial
    except (CertificateValidationError, OSError):
        return None


def _run_step(argv: Sequence[str], runner) -> None:
    _run(list(argv), runner)


# ---- transacción --------------------------------------------------------


def _rollback(
    config: AgentConfig,
    hostname: str,
    backup: Backup,
    *,
    runner,
    verifier: Verifier,
    warnings: list[str],
) -> bool:
    try:
        restore_backup(config, hostname, backup)
    except (OSError, InstallError) as exc:
        warnings.append(f"No se pudo restaurar el backup: {safe_error(str(exc))}")
    for command in (
        config.postfix.reload_command,
        config.dovecot.reload_command,
    ):
        try:
            _run_step(command, runner)
        except SniError as exc:
            warnings.append(safe_error(str(exc)) or "Falló una recarga de rollback")
    try:
        return bool(verifier("all"))
    except Exception:  # noqa: BLE001 - el rollback debe reportar, no propagar
        warnings.append("La verificación posterior al rollback falló")
        return False


def transactional_install(
    config: AgentConfig,
    hostname: str,
    lineage: Path,
    *,
    runner,
    verifier: Verifier | None = None,
    connector=None,
    job_id: str | None = None,
    previous_state: dict | None = None,
) -> InstallOutcome:
    """Instala con rollback. Nunca ejecuta Certbot ni toca cuentas/maildir (I8)."""

    config.require_reload_config()
    job = job_id or uuid.uuid4().hex
    lock_path = config.locks_dir / f"{hostname}.lock"
    with try_file_lock(lock_path) as acquired:
        if not acquired:
            raise InstallBusy(f"Otra instalación está en curso para {hostname}")
        check = verifier or default_verifier(config, hostname, connector=connector)
        previous = previous_state if previous_state is not None else read_json(config.state_path)
        try:
            generation = create_generation(config, hostname, lineage, runner=runner)
        except (InstallError, CertificateValidationError) as exc:
            return InstallOutcome(
                hostname=hostname,
                ok=False,
                changed=False,
                previous_generation=current_generation(config, hostname),
                error=safe_error(str(exc)) or "Validación de staging fallida",
            )

        backup = create_backup(
            config, hostname, job_id=job, previous_state=previous
        )
        warnings: list[str] = []
        try:
            activate_generation(config, hostname, generation)
            entries = installed_entries(config)
            write_sni_files(config, entries)
            if config.postfix.postmap_command:
                _run_step(config.postfix.postmap_command, runner)
            _run_step(config.postfix.reload_command, runner)
            if not check("postfix"):
                raise InstallError("Postfix no verifica 465/587 tras la activación")
            _run_step(config.dovecot.reload_command, runner)
            if not check("dovecot"):
                raise InstallError("Dovecot no verifica 993 tras la activación")
            if not check("all"):
                raise InstallError("La verificación SNI/sin SNI falló tras la activación")
        except ACTIVATION_ERRORS as exc:
            warnings.append(safe_error(str(exc)) or "Fallo de activación")
            rolled = _rollback(
                config, hostname, backup, runner=runner, verifier=check, warnings=warnings
            )
            return InstallOutcome(
                hostname=hostname,
                ok=False,
                changed=False,
                generation=generation,
                previous_generation=backup.previous_generation,
                rolled_back=rolled,
                error=safe_error(str(exc)) or "Fallo de activación",
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001 - cualquier fallo 5-10 debe revertir
            warnings.append(safe_error(str(exc)) or "Fallo inesperado de activación")
            rolled = _rollback(
                config, hostname, backup, runner=runner, verifier=check, warnings=warnings
            )
            return InstallOutcome(
                hostname=hostname,
                ok=False,
                changed=False,
                generation=generation,
                previous_generation=backup.previous_generation,
                rolled_back=rolled,
                error=safe_error(str(exc)) or "Fallo inesperado de activación",
                warnings=warnings,
            )

        # Paso 11: commit. Un fallo de poda no revierte (I9).
        try:
            _prune(config, hostname)
        except OSError as exc:
            warnings.append(f"Poda de generaciones fallida: {safe_error(str(exc))}")
        return InstallOutcome(
            hostname=hostname,
            ok=True,
            changed=True,
            generation=generation,
            previous_generation=backup.previous_generation,
            warnings=warnings,
        )


def _prune(config: AgentConfig, hostname: str) -> None:
    active = current_generation(config, hostname)
    names = _generations(config, hostname)
    keep = set(names[-KEEP_GENERATIONS:]) | ({active} if active else set())
    for name in names:
        if name not in keep:
            shutil.rmtree(generation_root(config, hostname) / name, ignore_errors=True)


def rollback_hostname(
    config: AgentConfig,
    hostname: str,
    *,
    runner,
    verifier: Verifier | None = None,
    connector=None,
) -> InstallOutcome:
    """Revierte explícitamente a la última generación anterior."""

    config.require_reload_config()
    lock_path = config.locks_dir / f"{hostname}.lock"
    with try_file_lock(lock_path) as acquired:
        if not acquired:
            raise InstallBusy(f"Otra instalación está en curso para {hostname}")
        check = verifier or default_verifier(config, hostname, connector=connector)
        active = current_generation(config, hostname)
        target = previous_generation(config, hostname)
        if target is None:
            return InstallOutcome(
                hostname=hostname,
                ok=False,
                changed=False,
                generation=active,
                error="No hay una generación anterior para revertir",
            )
        warnings: list[str] = []
        try:
            activate_generation(config, hostname, target)
            entries = installed_entries(config)
            write_sni_files(config, entries)
            if config.postfix.postmap_command:
                _run_step(config.postfix.postmap_command, runner)
            _run_step(config.postfix.reload_command, runner)
            _run_step(config.dovecot.reload_command, runner)
            if not check("all"):
                raise InstallError("La verificación posterior al rollback falló")
        except ACTIVATION_ERRORS as exc:
            return InstallOutcome(
                hostname=hostname,
                ok=False,
                changed=False,
                generation=target,
                previous_generation=active,
                error=safe_error(str(exc)) or "Rollback fallido",
                warnings=warnings,
            )
        return InstallOutcome(
            hostname=hostname,
            ok=True,
            changed=True,
            generation=target,
            previous_generation=active,
            warnings=warnings,
        )
