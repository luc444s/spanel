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
            self.say(f"transport: {selection.transport} ({selection.reason})")
        except Exception as exc:  # noqa: BLE001 - el doctor reporta, no propaga
            checks.append(DoctorCheck("transport", False, str(exc)))
            self.say(f"transport: not available ({exc})")
        record("docker", self._check_docker)
        if self.is_internal:
            for name in dict.fromkeys(
                (
                    self._traefik_container(),
                    "spanel-step-ca",
                    self.settings.dns_container,
                )
            ):
                record(f"container {name}", lambda n=name: self._check_container(n))
        record("dynamic directory", self._check_config_dir)
        record("resolver", self._check_resolver)
        if self.settings.ca_file:
            record("local CA", self._check_ca)
        if self.is_internal:
            record("HTTPS port", self._check_https_port)
        record("audit log", self._check_log)

        self.say()

        width = max(len(check.label) for check in checks)
        failed = 0
        for check in checks:
            status = "ok" if check.ok else "FAIL"
            if not check.ok:
                failed += 1
            detail = f"  {check.detail}" if check.detail else ""
            self.say(f"  [{status:5}] {check.label.ljust(width)}{detail}")

        if failed:
            self.say()
            self.say(f"doctor: {failed} check(s) failed")
            return EXIT_TRANSPORT
        self.say()
        self.say(f"doctor: all good (profile {self.settings.profile})")
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
            return True, "daemon reachable"
        return False, (result.text().strip().splitlines() or ["no response"])[-1]

    def _check_container(self, name: str) -> tuple[bool, str]:
        if not self.container_exists(name):
            return False, "does not exist"
        if not self.container_running(name):
            return False, "stopped"
        return True, "running"

    def _check_config_dir(self) -> tuple[bool, str]:
        return self.files.writable()

    def _check_resolver(self) -> tuple[bool, str]:
        if self.uses_docker:
            selection = self.selection
            if selection.cert_resolver in selection.resolvers:
                return True, (
                    f"{selection.cert_resolver} in {selection.traefik_container}"
                )
            return False, (
                f"missing '{selection.cert_resolver}' in {selection.traefik_container}"
            )
        path = self.settings.traefik_static
        if path is None:
            return False, "no static config path"
        text = self._read_raw(path)
        if text is None:
            return False, f"could not read {path}"
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            return False, f"invalid YAML: {exc}"
        resolvers = data.get("certificatesResolvers") or {}
        if self.settings.cert_resolver in resolvers:
            return True, f"{self.settings.cert_resolver} in {path.name}"
        return False, f"missing '{self.settings.cert_resolver}' in {path}"

    def _check_ca(self) -> tuple[bool, str]:
        path = self.settings.ca_file
        if path is None:
            return False, "no CA path"
        if not self._path_exists(path):
            return False, f"{path} does not exist (did you run traefik-lab.sh up?)"
        text = self._read_raw(path) or ""
        try:
            from cryptography import x509

            cert = x509.load_pem_x509_certificate(text.encode())
        except Exception as exc:  # noqa: BLE001
            return False, f"unreadable certificate: {exc}"
        return True, cert.subject.rfc4514_string()

    def _check_https_port(self) -> tuple[bool, str]:
        port = self.published_port(self._traefik_container(), 443)
        if port is None:
            return False, "the lab does not publish 443"
        return True, f"127.0.0.1:{port}"

    def _check_log(self) -> tuple[bool, str]:
        try:
            self.settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, str(exc)
        return True, str(self.settings.log_file)

    # ---- add --------------------------------------------------------------

