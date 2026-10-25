"""Comprobaciones de entorno del proxy (doctor)."""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

from .core import EXIT_OK, EXIT_TRANSPORT


@dataclass
class DoctorCheck:
    label: str
    ok: bool
    detail: str = ""


class DoctorMixin:
    def doctor(self) -> int:
        checks: list[DoctorCheck] = []

        def record(label: str, fn) -> None:
            try:
                ok, detail = fn()
            except Exception as exc:  # noqa: BLE001 - el doctor reporta, no propaga
                ok, detail = False, str(exc)
            checks.append(DoctorCheck(label, bool(ok), detail))

        record("docker", self._check_docker)
        if self.settings.is_internal:
            for name in dict.fromkeys(
                (
                    self.settings.traefik_container,
                    "spanel-step-ca",
                    self.settings.dns_container,
                )
            ):
                record(f"contenedor {name}", lambda n=name: self._check_container(n))
        record("directorio dinámico", self._check_config_dir)
        record("resolver", self._check_resolver)
        if self.settings.ca_file:
            record("CA local", self._check_ca)
        if self.settings.is_internal:
            record("puerto HTTPS", self._check_https_port)
        record("registro de auditoría", self._check_log)

        width = max(len(check.label) for check in checks)
        failed = 0
        for check in checks:
            status = "ok" if check.ok else "FALLA"
            if not check.ok:
                failed += 1
            detail = f"  {check.detail}" if check.detail else ""
            self.say(f"  [{status:5}] {check.label.ljust(width)}{detail}")

        if failed:
            self.say()
            self.say(f"doctor: {failed} comprobación(es) fallaron")
            return EXIT_TRANSPORT
        self.say()
        self.say(f"doctor: todo listo (perfil {self.settings.profile})")
        return EXIT_OK

    def _check_docker(self) -> tuple[bool, str]:
        result = self.runner.run(["docker", "info"])
        if result.ok:
            return True, "daemon accesible"
        return False, (result.text().strip().splitlines() or ["sin respuesta"])[-1]

    def _check_container(self, name: str) -> tuple[bool, str]:
        if not self.container_exists(name):
            return False, "no existe"
        if not self.container_running(name):
            return False, "detenido"
        return True, "running"

    def _check_config_dir(self) -> tuple[bool, str]:
        directory = self.settings.config_dir
        if self.settings.is_remote:
            result = self.runner.run(["test", "-w", str(directory)])
            return result.ok, str(directory)
        if not directory.exists():
            return False, f"no existe {directory}"
        if not os.access(directory, os.W_OK):
            return False, f"sin permiso de escritura en {directory}"
        return True, str(directory)

    def _check_resolver(self) -> tuple[bool, str]:
        path = self.settings.traefik_static
        if path is None:
            return False, "sin ruta de config estática"
        text = self._read_text(path)
        if text is None:
            return False, f"no se pudo leer {path}"
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            return False, f"YAML inválido: {exc}"
        resolvers = data.get("certificatesResolvers") or {}
        if self.settings.cert_resolver in resolvers:
            return True, f"{self.settings.cert_resolver} en {path.name}"
        return False, f"falta '{self.settings.cert_resolver}' en {path}"

    def _check_ca(self) -> tuple[bool, str]:
        path = self.settings.ca_file
        if path is None:
            return False, "sin ruta de CA"
        if not self._path_exists(path):
            return False, f"no existe {path} (¿corriste traefik-lab.sh up?)"
        text = self._read_text(path) or ""
        try:
            from cryptography import x509

            cert = x509.load_pem_x509_certificate(text.encode())
        except Exception as exc:  # noqa: BLE001
            return False, f"certificado ilegible: {exc}"
        return True, cert.subject.rfc4514_string()

    def _check_https_port(self) -> tuple[bool, str]:
        port = self.published_port(self.settings.traefik_container, 443)
        if port is None:
            return False, "el laboratorio no publica 443"
        return True, f"127.0.0.1:{port}"

    def _check_log(self) -> tuple[bool, str]:
        try:
            self.settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, str(exc)
        return True, str(self.settings.log_file)

    # ---- add --------------------------------------------------------------

