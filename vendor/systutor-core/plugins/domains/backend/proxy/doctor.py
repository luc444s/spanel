"""Comprobaciones de entorno del proxy (doctor)."""

from __future__ import annotations

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

        try:
            selection = self.selection
            self.say(f"transporte: {selection.transport} ({selection.reason})")
        except Exception as exc:  # noqa: BLE001 - el doctor reporta, no propaga
            checks.append(DoctorCheck("transporte", False, str(exc)))
            self.say(f"transporte: no disponible ({exc})")
        record("docker", self._check_docker)
        if self.is_internal:
            for name in dict.fromkeys(
                (
                    self._traefik_container(),
                    "spanel-step-ca",
                    self.settings.dns_container,
                )
            ):
                record(f"contenedor {name}", lambda n=name: self._check_container(n))
        record("directorio dinámico", self._check_config_dir)
        record("resolver", self._check_resolver)
        if self.settings.ca_file:
            record("CA local", self._check_ca)
        if self.is_internal:
            record("puerto HTTPS", self._check_https_port)
        record("registro de auditoría", self._check_log)

        self.say()

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

    def _traefik_container(self) -> str:
        if self.settings.transport == "docker":
            try:
                return self.selection.traefik_container or self.settings.traefik_container
            except Exception:  # noqa: BLE001 - el doctor muestra el error por su cuenta
                return self.settings.traefik_container
        return self.settings.traefik_container

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
        return self.files.writable()

    def _check_resolver(self) -> tuple[bool, str]:
        if self.uses_docker:
            selection = self.selection
            if selection.cert_resolver in selection.resolvers:
                return True, (
                    f"{selection.cert_resolver} en {selection.traefik_container}"
                )
            return False, (
                f"falta '{selection.cert_resolver}' en {selection.traefik_container}"
            )
        path = self.settings.traefik_static
        if path is None:
            return False, "sin ruta de config estática"
        text = self._read_raw(path)
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
        text = self._read_raw(path) or ""
        try:
            from cryptography import x509

            cert = x509.load_pem_x509_certificate(text.encode())
        except Exception as exc:  # noqa: BLE001
            return False, f"certificado ilegible: {exc}"
        return True, cert.subject.rfc4514_string()

    def _check_https_port(self) -> tuple[bool, str]:
        port = self.published_port(self._traefik_container(), 443)
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

