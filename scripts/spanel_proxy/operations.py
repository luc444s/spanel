"""Operaciones del CLI: doctor, add, list, status y remove."""

from __future__ import annotations

import os
import shlex
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import yaml

from .config import Settings
from .core import (
    EXIT_CERT,
    EXIT_DNS,
    EXIT_OK,
    EXIT_TRANSPORT,
    EXIT_VALIDATION,
    EXIT_WORDPRESS,
    CommandResult,
    ProxyError,
    atomic_write,
    document_container,
    document_domains,
    document_resolver,
    is_managed_document,
    normalize_domain,
    parse_site_document,
    probe_certificate,
    render_site_yaml,
    site_file_name,
    slug_for,
    timestamp,
    validate_managed_path,
)


@dataclass
class DoctorCheck:
    label: str
    ok: bool
    detail: str = ""


class ProxyOperations:
    def __init__(
        self,
        settings: Settings,
        runner,
        out: TextIO | None = None,
        err: TextIO | None = None,
    ) -> None:
        self.settings = settings
        self.runner = runner
        self.out = out or sys.stdout
        self.err = err or sys.stderr

    # ---- utilidades -------------------------------------------------------

    def say(self, message: str = "") -> None:
        print(message, file=self.out)

    def warn(self, message: str) -> None:
        print(message, file=self.err)

    def docker(self, *args: str, check: bool = True) -> CommandResult:
        result = self.runner.run(["docker", *args])
        if check and not result.ok:
            raise ProxyError(
                f"docker {args[0]} falló: {result.text().strip()}", EXIT_TRANSPORT
            )
        return result

    def container_running(self, name: str) -> bool:
        result = self.docker("inspect", "-f", "{{.State.Running}}", name, check=False)
        return result.ok and result.stdout.strip() == "true"

    def container_exists(self, name: str) -> bool:
        return self.docker("inspect", name, check=False).ok

    def container_networks(self, name: str) -> list[str]:
        result = self.docker(
            "inspect",
            "-f",
            "{{range $k, $v := .NetworkSettings.Networks}}{{$k}}\n{{end}}",
            name,
            check=False,
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def container_ip(self, name: str, network: str | None = None) -> str:
        network = network or self.settings.network
        result = self.docker(
            "inspect",
            "-f",
            f'{{{{(index .NetworkSettings.Networks "{network}").IPAddress}}}}',
            name,
            check=False,
        )
        if not result.ok:
            return ""
        return result.stdout.strip()

    def published_port(self, container: str, port: int) -> int | None:
        result = self.docker("port", container, f"{port}/tcp", check=False)
        if not result.ok:
            return None
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            _, _, host_side = line.rpartition(":")
            if host_side.isdigit():
                return int(host_side)
        return None

    def https_port(self) -> int:
        if self.settings.is_internal:
            published = self.published_port(self.settings.traefik_container, 443)
            if published:
                return published
        return self.settings.fallback_https_port

    def http_port(self) -> int:
        if self.settings.is_internal:
            published = self.published_port(self.settings.traefik_container, 80)
            if published:
                return published
        return self.settings.fallback_http_port

    def link_for(self, domain: str) -> str:
        port = self.https_port()
        return f"https://{domain}" if port == 443 else f"https://{domain}:{port}"

    def probe_host(self) -> str:
        """Dónde escuchar el handshake TLS: el host Docker o la máquina local."""
        return self.settings.ssh_host or "127.0.0.1"

    def _read_text(self, path: Path) -> str | None:
        if self.settings.is_remote:
            result = self.runner.run(["cat", str(path)])
            return result.stdout if result.ok else None
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def _path_exists(self, path: Path) -> bool:
        if self.settings.is_remote:
            return self.runner.run(["test", "-e", str(path)]).ok
        return path.exists()

    def _write_text(self, path: Path, text: str, mode: int = 0o644) -> None:
        if not self.settings.is_remote:
            atomic_write(path, text, mode)
            return
        quoted = shlex.quote(str(path))
        tmp = shlex.quote(f"{path}.tmp.{os.getpid()}")
        script = (
            f"umask 022 && cat > {tmp} && chmod {mode:o} {tmp} && mv {tmp} {quoted}"
        )
        result = self.runner.run(["sh", "-c", script], stdin=text)
        if not result.ok:
            raise ProxyError(
                f"no se pudo escribir {path} en {self.settings.ssh_host}: "
                f"{result.text().strip()}",
                EXIT_TRANSPORT,
            )

    def _delete_file(self, path: Path) -> None:
        if self.settings.is_remote:
            result = self.runner.run(["rm", "-f", "--", str(path)])
            if not result.ok:
                raise ProxyError(f"no se pudo borrar {path}", EXIT_TRANSPORT)
            return
        path.unlink(missing_ok=True)

    def site_files(self) -> list[Path]:
        if self.settings.is_remote:
            result = self.runner.run(
                [
                    "sh",
                    "-c",
                    (
                        f"ls -1 {shlex.quote(str(self.settings.config_dir))}/spanel-*.yml "
                        "2>/dev/null || true"
                    ),
                ]
            )
            return [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]
        directory = self.settings.config_dir
        if not directory.is_dir():
            return []
        return sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and path.name.startswith("spanel-") and path.suffix == ".yml"
        )

    def audit(self, action: str, site: str, domain: str, result: str) -> None:
        line = (
            f"{timestamp()}\taction={action}\tsite={site}\tdomain={domain}\tresult={result}\n"
        )
        try:
            self.settings.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.settings.log_file.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass

    # ---- doctor -----------------------------------------------------------

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

    def add(
        self,
        site: str,
        domain: str,
        *,
        www: bool = False,
        dry_run: bool = False,
        set_wordpress_url: bool = False,
        open_browser: bool = False,
        wait: int | None = None,
    ) -> int:
        domain = normalize_domain(domain, self.settings.profile)
        new_domains = [domain]
        if www:
            new_domains.append(normalize_domain(f"www.{domain}", self.settings.profile))

        if not self.container_exists(site):
            raise ProxyError(f"el sitio '{site}' no existe como contenedor", EXIT_TRANSPORT)
        if not self.container_running(site):
            raise ProxyError(f"el sitio '{site}' no está running", EXIT_TRANSPORT)

        slug = slug_for(site)
        path = self.settings.config_dir / site_file_name(slug)
        existing_text = self._read_text(path)
        existing_domains: list[str] = []
        if existing_text:
            if not is_managed_document(existing_text):
                raise ProxyError(f"{path} existe y no está gestionado por spanel", EXIT_VALIDATION)
            existing_domains = document_domains(parse_site_document(existing_text))

        owner = self._find_owner(domain)
        if owner is not None and owner != path:
            raise ProxyError(
                f"el dominio {domain} ya está gestionado en {owner.name}", EXIT_VALIDATION
            )

        domains = list(dict.fromkeys([*existing_domains, *new_domains]))
        content = render_site_yaml(
            site,
            domains,
            self.settings.cert_resolver,
            self.settings.entrypoint_http,
            self.settings.entrypoint_https,
        )

        connect_commands = []
        if self.settings.network not in self.container_networks(site):
            connect_commands.append(
                f"docker network connect {self.settings.network} {site}"
            )
        dns_commands = self._dns_plan(new_domains)

        if dry_run:
            self.say("# dry-run: no se escribe nada")
            self.say(f"# archivo: {path}")
            self.say(f"# resolver: {self.settings.cert_resolver}")
            for command in connect_commands:
                self.say(f"# comando: {command}")
            for line in dns_commands:
                self.say(f"# {line}")
            self.say()
            self.say(content)
            return EXIT_OK

        for command in connect_commands:
            self.warn(f"==> {command}")
            self.docker(*shlex.split(command)[1:])
        if self.settings.is_internal:
            self._dns_apply(new_domains, add=True)

        if existing_text is not None and existing_text != content:
            self._backup(path, existing_text)
        self._write_text(path, content)
        self.audit("add", site, domain, "written")

        wait_seconds = self.settings.wait_seconds if wait is None else wait
        if wait_seconds > 0:
            self._wait_for_certificate(domain, wait_seconds)

        if set_wordpress_url:
            self._update_wordpress(site, domain)

        port = self.https_port()
        self.say(f"sitio:    {site}")
        self.say(f"dominio:  {', '.join(domains)}")
        self.say(f"archivo:  {path}")
        self.say(f"resolver: {self.settings.cert_resolver}")
        self.say(
            "estado:   active"
            if wait_seconds > 0
            else "estado:   pendiente (no verificado; usá 'status')"
        )
        self.say(f"url:      {self.link_for(domain)}")
        if port != 443:
            self.say(f"nota:     el laboratorio publica HTTPS en 127.0.0.1:{port}")
        self.say()
        self.say("Para abrir el link en este host, agregá una vez:")
        for name in domains:
            self.say(f"  127.0.0.1 {name}")
        self.say(f"(en /etc/hosts; después: {self.link_for(domain)})")
        if self.settings.ca_file:
            self.say()
            self.say("Y confiá en la CA local una sola vez:")
            self.say(f"  sudo cp {self.settings.ca_file} /usr/local/share/ca-certificates/spanel-lab-ca.crt")
            self.say("  sudo update-ca-certificates")
            self.say(f"  curl --cacert {self.settings.ca_file} {self.link_for(domain)}")
        if open_browser:
            import webbrowser

            url = self.link_for(domain)
            try:
                opened = webbrowser.open(url)
            except Exception as exc:  # noqa: BLE001
                opened = False
                self.warn(f"aviso: no se pudo abrir el navegador: {exc}")
            if not opened:
                self.warn(f"aviso: abrí manualmente {url}")
        return EXIT_OK

    def _wait_for_certificate(self, domain: str, seconds: int) -> None:
        port = self.https_port()
        host = self.probe_host()
        deadline = time.monotonic() + max(1, seconds)
        last = None
        while time.monotonic() < deadline:
            last = probe_certificate(domain, host, port, self.settings.ca_file)
            if last.state == "active":
                self.say(f"certificado: emitido por {last.issuer}")
                return
            time.sleep(2)
        detail = last.error if last else "sin respuesta"
        raise ProxyError(
            f"el certificado para {domain} no quedó activo en {seconds}s ({detail})",
            EXIT_CERT,
        )

    def _update_wordpress(self, site: str, domain: str) -> None:
        url = self.link_for(domain)
        for option in ("home", "siteurl"):
            result = self.docker(
                "run",
                "--rm",
                "--volumes-from",
                site,
                "--network",
                f"container:{site}",
                self.settings.wp_image,
                "option",
                "update",
                option,
                url,
                f"--path={self.settings.wp_root}",
                "--allow-root",
                check=False,
            )
            if not result.ok:
                raise ProxyError(
                    f"no se pudo actualizar WordPress ({option}): {result.text().strip()}",
                    EXIT_WORDPRESS,
                )
        self.audit("wordpress", site, domain, "updated")
        self.say(f"wordpress: home y siteurl = {url}")

    # ---- DNS interno ------------------------------------------------------

    def _dns_plan(self, domains: Sequence[str]) -> list[str]:
        if not self.settings.is_internal or self.settings.dns_hosts_file is None:
            return []
        return [f"dns: agregar {domain} en {self.settings.dns_hosts_file}" for domain in domains]

    def _dns_apply(self, domains: Sequence[str], *, add: bool) -> None:
        path = self.settings.dns_hosts_file
        if not self.settings.is_internal or path is None:
            return
        ip = self.container_ip(self.settings.traefik_container)
        if add and not ip:
            raise ProxyError(
                "no se pudo obtener la IP del Traefik del laboratorio", EXIT_TRANSPORT
            )
        text = self._read_text(path) or ""
        targets = set(domains)
        kept: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                kept.append(line)
                continue
            parts = stripped.split()
            if len(parts) >= 2 and parts[-1] in targets:
                continue
            kept.append(line)
        if add:
            for domain in domains:
                kept.append(f"{ip} {domain}")
        self._write_text(path, "\n".join(kept).rstrip("\n") + "\n")
        self.docker("kill", "-s", "HUP", self.settings.dns_container, check=False)

    # ---- búsqueda y respaldo ---------------------------------------------

    def _find_owner(self, domain: str) -> Path | None:
        for path in self.site_files():
            text = self._read_text(path)
            if not text or not is_managed_document(text):
                continue
            try:
                data = parse_site_document(text)
            except ProxyError:
                continue
            if domain in document_domains(data):
                return path
        return None

    def _backup(self, path: Path, text: str) -> None:
        target = self.settings.backups_dir / timestamp() / path.name
        self._write_text(target, text)
        self.warn(f"==> backup: {target}")

    # ---- status -----------------------------------------------------------

    def status(self, domain: str) -> int:
        domain = normalize_domain(domain, self.settings.profile)
        owner = self._find_owner(domain)
        if owner is None:
            raise ProxyError(f"el dominio {domain} no está gestionado por spanel", EXIT_VALIDATION)
        text = self._read_text(owner) or ""
        data = parse_site_document(text)
        site = document_container(data) or "desconocido"
        resolver = document_resolver(data) or self.settings.cert_resolver
        port = self.https_port()
        host = self.probe_host()
        info = probe_certificate(domain, host, port, self.settings.ca_file)

        self.say(f"dominio:   {domain}")
        self.say(f"sitio:     {site}")
        self.say(f"archivo:   {owner}")
        self.say(f"resolver:  {resolver}")
        self.say(f"estado:    {info.state}")
        if info.issuer:
            self.say(f"emisor:    {info.issuer}")
        if info.subject:
            self.say(f"sujeto:    {info.subject}")
        if info.not_after:
            self.say(f"notAfter:  {info.not_after}")
        if info.sans:
            self.say(f"SAN:       {', '.join(info.sans)}")
        self.say(f"url:       {self.link_for(domain)}")
        if info.error:
            self.say(f"detalle:   {info.error}")

        if info.state == "active":
            return EXIT_OK
        if info.state == "pending_dns":
            return EXIT_DNS
        return EXIT_CERT

    # ---- list -------------------------------------------------------------

    def list_domains(self) -> int:
        rows: list[tuple[str, str, str, str]] = []
        for path in self.site_files():
            text = self._read_text(path)
            if not text or not is_managed_document(text):
                continue
            try:
                data = parse_site_document(text)
            except ProxyError:
                continue
            site = document_container(data) or "?"
            resolver = document_resolver(data) or "?"
            for domain in document_domains(data):
                rows.append((domain, site, resolver, path.name))
        if not rows:
            self.say("no hay dominios gestionados")
            return EXIT_OK
        self.say(f"{'DOMINIO':<40} {'SITIO':<28} {'RESOLVER':<12} ARCHIVO")
        for domain, site, resolver, name in sorted(rows):
            self.say(f"{domain:<40} {site:<28} {resolver:<12} {name}")
        return EXIT_OK

    # ---- remove -----------------------------------------------------------

    def remove(self, domain: str, *, dry_run: bool = False) -> int:
        domain = normalize_domain(domain, self.settings.profile)
        owner = self._find_owner(domain)
        if owner is None:
            raise ProxyError(f"el dominio {domain} no está gestionado por spanel", EXIT_VALIDATION)
        validate_managed_path(owner)

        text = self._read_text(owner) or ""
        data = parse_site_document(text)
        domains = document_domains(data)
        remaining = [item for item in domains if item not in (domain, f"www.{domain}")]
        site = document_container(data) or ""
        resolver = document_resolver(data) or self.settings.cert_resolver

        if dry_run:
            self.say(f"# dry-run: {owner}")
            self.say(f"# se quitaría: {domain}")
            self.say(f"# quedarían: {', '.join(remaining) if remaining else '(ninguno, se borra el archivo)'}")
            return EXIT_OK

        self._backup(owner, text)
        if remaining:
            content = render_site_yaml(
                site,
                remaining,
                resolver,
                self.settings.entrypoint_http,
                self.settings.entrypoint_https,
            )
            self._write_text(owner, content)
            action = f"updated ({', '.join(remaining)})"
        else:
            self._delete_file(owner)
            action = "deleted"

        if self.settings.is_internal:
            self._dns_apply([domain, f"www.{domain}"], add=False)

        self.audit("remove", site, domain, action)
        self.say(f"dominio:  {domain}")
        self.say(f"archivo:  {owner} -> {action}")
        self.say("estado:   eliminado")
        return EXIT_OK
