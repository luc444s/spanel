"""Operaciones de dominios: add, remove, status, list y utilidades."""

from __future__ import annotations

import logging
import shlex
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .config import Settings
from .core import (
    EXIT_CERT,
    EXIT_DNS,
    EXIT_OK,
    EXIT_TRANSPORT,
    EXIT_VALIDATION,
    ProxyError,
    document_container,
    document_domains,
    document_resolver,
    is_managed_document,
    normalize_domain,
    parse_site_document,
    render_site_yaml,
    site_file_name,
    slug_for,
    timestamp,
    validate_managed_path,
)
from .detect import Detection, select_transport
from .doctor import DoctorMixin
from .files import ProxyFiles, build_files
from .labdns import LabDnsMixin
from .probe import CertificateInfo, probe_certificate
from .transport import CommandResult
from .wordpress import WordPressMixin

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ManagedDomain:
    domain: str
    container: str
    resolver: str
    file: str


class ProxyOperations(DoctorMixin, LabDnsMixin, WordPressMixin):
    def __init__(
        self,
        settings: Settings,
        runner,
        out: TextIO | None = None,
        err: TextIO | None = None,
        files: ProxyFiles | None = None,
    ) -> None:
        self.settings = settings
        self.runner = runner
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self._files = files
        self._detection: Detection | None = None
        self._detection_error: ProxyError | None = None

    # ---- transporte -------------------------------------------------------

    @property
    def selection(self) -> Detection:
        """Detección resuelta una sola vez por operación (I3)."""
        if self._detection is None:
            if self._detection_error is not None:
                raise self._detection_error
            try:
                detection = select_transport(self.settings, self.runner)
            except ProxyError as exc:
                self._detection_error = exc
                raise
            logger.info(
                "domains proxy transport=%s reason=%s",
                detection.transport,
                detection.reason,
            )
            self._detection = detection
        return self._detection

    @property
    def files(self) -> ProxyFiles:
        if self._files is None:
            self._files = build_files(self.runner, self.selection)
        return self._files

    def _lenient_selection(self) -> Detection | None:
        """`auto` sin transporte no rompe la lectura: lista vacía como 0027."""
        try:
            return self.selection
        except ProxyError:
            if self.settings.transport == "auto":
                return None
            raise

    @property
    def profile(self) -> str:
        selection = self._lenient_selection()
        if selection is not None and selection.transport == "docker" and selection.profile:
            return selection.profile
        return self.settings.profile

    @property
    def is_internal(self) -> bool:
        return self.profile == "internal"

    @property
    def resolver(self) -> str:
        selection = self._lenient_selection()
        if (
            selection is not None
            and selection.transport == "docker"
            and selection.cert_resolver
        ):
            return selection.cert_resolver
        return self.settings.cert_resolver

    @property
    def uses_docker(self) -> bool:
        selection = self._lenient_selection()
        return selection is not None and selection.transport == "docker"

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
        selection = self._lenient_selection()
        if selection is not None and selection.transport == "docker":
            if selection.probe_port:
                return selection.probe_port
            return self.settings.fallback_https_port
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
        """Dónde escuchar el handshake TLS: detección Docker, override o localhost."""
        selection = self._lenient_selection()
        if selection is not None and selection.transport == "docker":
            return selection.probe_host or ""
        return self.settings.probe_address or self.settings.ssh_host or "127.0.0.1"

    def config_writable(self) -> tuple[bool, str]:
        """¿Se puede escribir el directorio dinámico desde este proceso?"""
        try:
            return self.files.writable()
        except ProxyError as exc:
            return False, str(exc)

    # ---- archivos ---------------------------------------------------------

    def _read_text(self, path: Path) -> str | None:
        return self.files.read_text(path)

    def _read_raw(self, path: Path) -> str | None:
        """Lectura de config estática/CA fuera del file provider (no gestionada)."""
        if self.settings.transport == "ssh" or (
            self.settings.transport == "auto" and self.settings.is_remote
        ):
            result = self.runner.run(["cat", str(path)])
            return result.stdout if result.ok else None
        try:
            return path.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError, OSError):
            return None

    def _path_exists(self, path: Path) -> bool:
        return self.files.exists(path)

    def _write_text(self, path: Path, text: str, mode: int = 0o644) -> None:
        self.files.write_text(path, text, mode)

    def _delete_file(self, path: Path) -> None:
        self.files.delete(path)

    def site_files(self) -> list[Path]:
        selection = self._lenient_selection()
        if selection is None:
            return []
        return self.files.list_site_files()

    def audit(self, action: str, site: str, domain: str, result: str) -> None:
        transport = (
            self._detection.transport if self._detection is not None else self.settings.transport
        )
        line = (
            f"{timestamp()}\taction={action}\tsite={site}\tdomain={domain}"
            f"\tresult={result}\ttransport={transport}\n"
        )
        try:
            self.settings.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.settings.log_file.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass

    # ---- doctor -----------------------------------------------------------

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
        domain = normalize_domain(domain, self.profile)
        new_domains = [domain]
        if www:
            new_domains.append(normalize_domain(f"www.{domain}", self.profile))

        if not self.container_exists(site):
            raise ProxyError(f"el sitio '{site}' no existe como contenedor", EXIT_TRANSPORT)
        if not self.container_running(site):
            raise ProxyError(f"el sitio '{site}' no está running", EXIT_TRANSPORT)

        slug = slug_for(site)
        path = self.files.config_dir / site_file_name(slug)
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
            self.resolver,
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
            self.say(f"# resolver: {self.resolver}")
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
        if self.is_internal and not self.uses_docker:
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
        self.say(f"resolver: {self.resolver}")
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
            self.say(
                f"  sudo cp {self.settings.ca_file} "
                "/usr/local/share/ca-certificates/spanel-lab-ca.crt"
            )
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
        deadline = time.monotonic() + max(1, seconds)
        last = None
        last_code = None
        while time.monotonic() < deadline:
            last = self.certificate(domain)
            if last.state == "active":
                last_code = self._route_code(domain)
                if last_code is not None and last_code not in (404, 503):
                    self.say(f"certificado: emitido por {last.issuer}")
                    self.say(f"ruta:       HTTP {last_code} en {self.link_for(domain)}")
                    return
            time.sleep(2)
        if last is None or last.state != "active":
            detail = last.error if last else "sin respuesta"
            raise ProxyError(
                f"el certificado para {domain} no quedó activo en {seconds}s ({detail})",
                EXIT_CERT,
            )
        raise ProxyError(
            f"el certificado está activo pero la ruta {domain} responde HTTP "
            f"{last_code or 'sin respuesta'} en {seconds}s",
            EXIT_CERT,
        )

    def _route_code(self, domain: str) -> int | None:
        """Código HTTP de `GET /` hablando TLS directo al proxy con SNI del dominio."""
        host = self.probe_host()
        if not host:
            return None
        context = ssl.create_default_context()
        if self.settings.ca_file:
            context.load_verify_locations(cafile=str(self.settings.ca_file))
        try:
            with (
                socket.create_connection((host, self.https_port()), timeout=5) as raw,
                context.wrap_socket(raw, server_hostname=domain) as tls,
            ):
                request = (
                    f"GET / HTTP/1.1\r\nHost: {domain}\r\n"
                    "Connection: close\r\nUser-Agent: spanel-proxy-domain\r\n\r\n"
                )
                tls.sendall(request.encode())
                data = tls.recv(128)
        except (OSError, ssl.SSLError):
            return None
        line = data.split(b"\r\n", 1)[0].split()
        if len(line) < 2 or not line[1].isdigit():
            return None
        return int(line[1])

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
        if not self.files.supports_foreign_paths:
            self.warn("==> backup: omitido (transporte docker sin escritura fuera del provider)")
            return
        target = self.settings.backups_dir / timestamp() / path.name
        self._write_text(target, text)
        self.warn(f"==> backup: {target}")

    # ---- status -----------------------------------------------------------

    def status(self, domain: str) -> int:
        domain = normalize_domain(domain, self.profile)
        owner = self._find_owner(domain)
        if owner is None:
            raise ProxyError(f"el dominio {domain} no está gestionado por spanel", EXIT_VALIDATION)
        text = self._read_text(owner) or ""
        data = parse_site_document(text)
        site = document_container(data) or "desconocido"
        resolver = document_resolver(data) or self.resolver
        info = self.certificate(domain)

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

    def managed_domains(self) -> list[ManagedDomain]:
        """Todos los dominios extra gestionados, leídos de los YAML."""
        rows: list[ManagedDomain] = []
        for path in self.site_files():
            text = self._read_text(path)
            if not text or not is_managed_document(text):
                continue
            try:
                data = parse_site_document(text)
            except ProxyError:
                continue
            container = document_container(data) or "?"
            resolver = document_resolver(data) or "?"
            for domain in document_domains(data):
                rows.append(ManagedDomain(domain, container, resolver, path.name))
        return rows

    def certificate(self, domain: str) -> CertificateInfo:
        """Sondea el certificado de un dominio ya conocido; no lo normaliza."""
        selection = self._lenient_selection()
        if selection is not None and selection.transport == "docker" and not selection.probe_host:
            return CertificateInfo(
                state="pending_cert",
                error="no hay host de sondeo alcanzable desde este contenedor",
            )
        return probe_certificate(
            domain,
            self.probe_host(),
            self.https_port(),
            self.settings.ca_file,
            timeout=self.settings.probe_timeout,
        )

    def list_domains(self) -> int:
        rows = self.managed_domains()
        if not rows:
            self.say("no hay dominios gestionados")
            return EXIT_OK
        self.say(f"{'DOMINIO':<40} {'SITIO':<28} {'RESOLVER':<12} ARCHIVO")
        for row in sorted(rows, key=lambda item: item.domain):
            self.say(f"{row.domain:<40} {row.container:<28} {row.resolver:<12} {row.file}")
        return EXIT_OK

    # ---- remove -----------------------------------------------------------

    def remove(self, domain: str, *, dry_run: bool = False) -> int:
        domain = normalize_domain(domain, self.profile)
        owner = self._find_owner(domain)
        if owner is None:
            raise ProxyError(f"el dominio {domain} no está gestionado por spanel", EXIT_VALIDATION)
        validate_managed_path(owner)

        text = self._read_text(owner) or ""
        data = parse_site_document(text)
        domains = document_domains(data)
        remaining = [item for item in domains if item not in (domain, f"www.{domain}")]
        site = document_container(data) or ""
        resolver = document_resolver(data) or self.resolver

        if dry_run:
            self.say(f"# dry-run: {owner}")
            self.say(f"# se quitaría: {domain}")
            kept = ", ".join(remaining) if remaining else "(ninguno, se borra el archivo)"
            self.say(f"# quedarían: {kept}")
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

        if self.is_internal and not self.uses_docker:
            self._dns_apply([domain, f"www.{domain}"], add=False)

        self.audit("remove", site, domain, action)
        self.say(f"dominio:  {domain}")
        self.say(f"archivo:  {owner} -> {action}")
        self.say("estado:   eliminado")
        return EXIT_OK

