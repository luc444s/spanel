"""Tenant-scoped site listing, WordPress users and one-password reset.

The router never builds SQL or Docker arguments. This module owns the
`hosting_sites` query, and the only site it ever operates on is the row that the
guard resolved for the authenticated tenant.

A password is generated, hashed with WordPress' own function, written to one
row by ID, read back by ID, and returned once. It is never stored by this
plugin and never written to the audit row or a log line.
"""

from __future__ import annotations

import re
import secrets
import shlex
from collections.abc import Callable
from urllib.parse import quote

from fastapi import FastAPI
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from systutor.contracts.domain_ownership import is_extra_claimed
from systutor.contracts.site_domain_publisher import get_site_domain_publisher
from systutor.contracts.site_namespace import domain_in_suffix, get_site_suffix
from systutor.core.errors import AppError, NotFoundError
from systutor.kernel.audit.service import record_audit
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Tenant

from .config import ENV_WHITELIST, RawAdapter, site_dir
from .database import (
    filas_de_usuarios,
    prefijo_de_tablas,
    resolver_alias_de_contenedor,
    roles_desde_capacidades,
    usuarios_con_rol,
)
from .database_binding import database_binding
from .database_binding import database_credentials as _database_credentials
from .inspection import candidato_de, inspect_container
from .models import HostingSite
from .network import (
    connect_args,
    multi_database_enabled,
    needs_connection,
    validate_network_name,
)
from .passwords import hash_de_password
from .phpmyadmin_proxy import PmaClient
from .provisioner import Provisioner, SiteNames, slug_for
from .reset import actualizar_hash, leer_hash
from .schemas import (
    CreateSiteResponse,
    HostingSiteListResponse,
    HostingSiteSummary,
    ResetPasswordResponse,
    SecretRotationResponse,
    SiteConfigResponse,
    SiteDatabaseResponse,
    SiteLogsResponse,
    WordPressUserListResponse,
    WordPressUserSummary,
)
from .settings import HostingSettings, docker_env, get_hosting_settings
from .static import LABEL_MANAGED, StaticNames, StaticProvisioner
from .transport import Fallo, build_docker_argv, run
from .wpconfig import read_wp_config

PASSWORD_GENERATED_BYTES = 12


def generar_password() -> str:
    """A fresh password, returned once and never persisted by this plugin."""
    return secrets.token_urlsafe(PASSWORD_GENERATED_BYTES)


def rol_mostrado(serializado: str) -> str:
    """The role exactly as WordPress returns it, including the empty case.

    The extracted helper uses `-` so its CLI table has a printable column. The
    API contract says a user without a capabilities row has an EMPTY role, so
    the placeholder is translated here.
    """
    role = roles_desde_capacidades(serializado)
    return "" if role == "-" else role


def estado_de_usuario(valor: str) -> int:
    """`user_status` is an integer in WordPress; keep its meaning untouched."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


def estado_del_contenedor(info: dict) -> str:
    state = info.get("State")
    state = state if isinstance(state, dict) else {}
    status = state.get("Status")
    return status.strip() if isinstance(status, str) and status.strip() else "unknown"


def database_credentials(info: dict, *, wp_config: str | None = None) -> tuple[str, str, str, str]:
    """The four values WordPress already uses, validated as a Docker target.

    Kept as a thin re-export of `database_binding` so the existing service API
    and its tests do not depend on the new module's location.
    """
    return _database_credentials(info, wp_config=wp_config)


def phpmyadmin_url(base: str, host: str) -> str | None:
    """The phpMyAdmin login page, or `None` when the panel has no URL.

    The password is never part of this URL. A deployment with a fixed
    `PMA_HOST` uses the base as-is; one with `PMA_ARBITRARY=1` can write
    `?server={server}` (or any other shape) and the database host is encoded
    into the placeholder.
    """
    base = base.strip()
    if not base or not re.match(r"^https?://", base, re.IGNORECASE):
        return None
    return base.replace("{server}", quote(host, safe=""))


class HostingService:
    def __init__(
        self,
        db: Session,
        *,
        settings: HostingSettings | None = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_hosting_settings()

    @property
    def _env(self) -> dict[str, str]:
        return docker_env(self._settings)

    def _inspect(self, container_name: str) -> dict | None:
        return inspect_container(
            self._env,
            container_name,
            timeout=self._settings.command_timeout,
        )

    def _require_site(self, site: HostingSite) -> tuple[dict, bool]:
        """The container must exist and still be a managed site.

        Returns the inspect payload and whether its `spanel.managed` label
        allows operations. WordPress keeps its historical detector; a static
        site is validated by the ownership label alone because nginx is not a
        WordPress container.
        """
        info = self._inspect(site.container_name)
        if info is None:
            raise AppError(
                f"El contenedor {site.container_name} no existe o no responde",
                status_code=409,
                code="hosting_site_unavailable",
            )
        if site.kind == "static":
            labels = ((info.get("Config") or {}).get("Labels")) or {}
            return info, labels.get(LABEL_MANAGED) != "false"
        candidato = candidato_de(info)
        if not candidato.es_sitio:
            raise AppError(
                f"El contenedor {site.container_name} ya no es un sitio WordPress",
                status_code=409,
                code="hosting_site_unavailable",
            )
        return info, candidato.gestionable

    def _require_wordpress(self, site: HostingSite) -> None:
        if site.kind == "static":
            raise AppError(
                f"El sitio {site.container_name} es estático; "
                "esta operación es solo para WordPress",
                status_code=409,
                code="hosting_site_wrong_kind",
            )

    def _wordpress_admin_email(
        self, tenant_context: TenantContext, domain: str, admin_email: str | None
    ) -> str:
        """The authenticated user's email, with `admin@<domain>` as fallback."""
        if admin_email:
            return admin_email
        user = self._db.get(User, tenant_context.current_user_id)
        email = (user.email or "").strip() if user is not None else ""
        return email or f"admin@{domain}"

    def _database_binding_for(
        self, site: HostingSite, info: dict
    ) -> tuple[str, str, str, str, str]:
        """Environment first; `wp-config.php` only when the environment fails.

        When the declared host is not a Docker container, its network alias is
        resolved inside the site's own networks. The raw host is returned
        untouched for WordPress; only the inspected container changes.
        """
        try:
            crudo, host, database, user, password = database_binding(info)
        except AppError:
            wp_config = read_wp_config(
                self._env,
                site.container_name,
                timeout=self._settings.command_timeout,
            )
            if wp_config is None:
                raise
            crudo, host, database, user, password = database_binding(
                info, wp_config=wp_config
            )
        if self._inspect(host) is None:
            resuelto = resolver_alias_de_contenedor(self._env, info, host)
            if resuelto is not None:
                host = resuelto
        return crudo, host, database, user, password

    def _managed_site(self, site: HostingSite) -> dict:
        info, managed = self._require_site(site)
        if not managed:
            raise AppError(
                f"El sitio {site.container_name} no es gestionable (spanel.managed=false)",
                status_code=409,
                code="hosting_site_not_managed",
            )
        return info

    def _audit_site(
        self, site: HostingSite, context: TenantContext, action: str, details: dict | None = None
    ) -> None:
        record_audit(
            self._db,
            tenant_id=context.current_tenant_id,
            branch_id=context.current_branch_id,
            actor_user_id=context.current_user_id,
            actor_type="user",
            module="hosting",
            action=f"hosting.site.{action}",
            entity_type="hosting_site",
            entity_id=site.id,
            result="success",
            correlation_id=None,
            request_id=None,
            details={"container_name": site.container_name, **(details or {})},
        )

    def create_site(
        self,
        tenant_context: TenantContext,
        name: str,
        domain: str,
        admin_email: str | None = None,
        *,
        kind: str = "wordpress",
        app: FastAPI | None = None,
    ) -> CreateSiteResponse:
        try:
            slug = slug_for(name)
        except ValueError as exc:
            raise AppError(str(exc), status_code=422, code="hosting_invalid_name") from exc
        names = StaticNames(slug) if kind == "static" else SiteNames(slug)
        container_name = names.container if kind == "static" else names.wp
        tenant_id = tenant_context.current_tenant_id
        tenant = self._db.get(Tenant, tenant_id)
        if tenant is None or not tenant.is_active:
            raise AppError(
                "Tenant inactivo o inexistente", status_code=409, code="hosting_tenant_inactive"
            )
        if self._db.scalar(
            select(HostingSite.id).where(
                (HostingSite.domain == domain) | (HostingSite.container_name == container_name)
            )
        ):
            raise AppError(
                "Dominio o nombre ya registrado", status_code=409, code="hosting_site_conflict"
            )
        if app is not None and is_extra_claimed(app, self._db, domain):
            raise AppError(
                "El dominio ya está publicado como dominio extra por el plugin de dominios",
                status_code=409,
                code="hosting_site_conflict",
            )
        if app is not None:
            suffix = get_site_suffix(app, self._db, tenant_id)
            if not suffix:
                raise AppError(
                    "El tenant no configuró un sufijo de sitios",
                    status_code=409,
                    code="hosting_site_suffix_missing",
                )
            if not domain_in_suffix(domain, suffix):
                raise AppError(
                    f"El dominio debe estar debajo de {suffix}",
                    status_code=409,
                    code="hosting_site_domain_outside_suffix",
                )
        if kind == "static":
            return self._create_static(
                tenant_context, names, tenant_id, domain, app=app
            )
        return self._create_wordpress(
            tenant_context, names, tenant_id, domain, admin_email
        )

    def _create_wordpress(
        self,
        tenant_context: TenantContext,
        names: SiteNames,
        tenant_id: str,
        domain: str,
        admin_email: str | None,
    ) -> CreateSiteResponse:
        provisioner = Provisioner(self._settings)
        email = self._wordpress_admin_email(tenant_context, domain, admin_email)
        try:
            password = provisioner.create(names, tenant_id, domain, email)
        except Fallo as exc:
            raise AppError(str(exc), status_code=409, code="hosting_provision_failed") from exc
        return self._finish_create(
            tenant_context,
            tenant_id=tenant_id,
            container_name=names.wp,
            domain=domain,
            kind="wordpress",
            password=password,
            cleanup=lambda: provisioner.cleanup(names, tenant_id),
        )

    def _create_static(
        self,
        tenant_context: TenantContext,
        names: StaticNames,
        tenant_id: str,
        domain: str,
        *,
        app: FastAPI | None,
    ) -> CreateSiteResponse:
        provisioner = StaticProvisioner(self._settings)
        publisher = get_site_domain_publisher(app)
        try:
            provisioner.create(
                names,
                tenant_id,
                domain,
                traefik=publisher is None and self._settings.traefik,
            )
        except Fallo as exc:
            failures = provisioner.cleanup(names, tenant_id)
            detail = f"; limpieza: {failures}" if failures else ""
            raise AppError(
                f"{exc}{detail}", status_code=409, code="hosting_provision_failed"
            ) from exc
        return self._finish_create(
            tenant_context,
            tenant_id=tenant_id,
            container_name=names.container,
            domain=domain,
            kind="static",
            password=None,
            cleanup=lambda: provisioner.cleanup(names, tenant_id),
            publisher=publisher,
        )

    def _finish_create(
        self,
        tenant_context: TenantContext,
        *,
        tenant_id: str,
        container_name: str,
        domain: str,
        kind: str,
        password: str | None,
        cleanup: Callable[[], list[str]],
        publisher=None,
    ) -> CreateSiteResponse:
        published = False
        if publisher is not None:
            try:
                publisher.publish(container_name, domain)
                published = True
            except Exception as exc:
                failures = cleanup()
                raise AppError(
                    f"No se pudo publicar el dominio: {exc}; limpieza: {failures}",
                    status_code=409,
                    code="hosting_provision_failed",
                ) from exc
        site = HostingSite(
            tenant_id=tenant_id,
            container_name=container_name,
            domain=domain,
            kind=kind,
        )
        try:
            self._db.add(site)
            self._db.flush()
            self._audit_site(site, tenant_context, "create", {"kind": kind})
            self._db.commit()
        except Exception as exc:
            self._db.rollback()
            if published:
                try:
                    publisher.unpublish(domain)
                except Exception:
                    # The row is gone either way; an orphan route is corrected
                    # by the operator or by deleting the site again.
                    pass
            failures = cleanup()
            if isinstance(exc, IntegrityError):
                raise AppError(
                    f"Dominio o nombre ya registrado; limpieza: {failures}",
                    status_code=409,
                    code="hosting_site_conflict",
                ) from exc
            raise AppError(
                f"No se pudo registrar el sitio; limpieza: {failures}",
                status_code=500,
                code="hosting_provision_failed",
            ) from exc
        return CreateSiteResponse(
            site=HostingSiteSummary(
                id=site.id,
                container_name=site.container_name,
                domain=site.domain,
                status="running",
                managed=True,
                kind=kind,
            ),
            admin_password=password,
        )

    def _operate(
        self, site: HostingSite, tenant_context: TenantContext, action: str
    ) -> HostingSiteSummary:
        self._managed_site(site)
        try:
            Provisioner(self._settings).command(action, site.container_name)
        except Fallo as exc:
            raise AppError(str(exc), status_code=409, code="hosting_site_unavailable") from exc
        self._audit_site(site, tenant_context, action)
        return HostingSiteSummary(
            id=site.id,
            container_name=site.container_name,
            domain=site.domain,
            status="exited" if action == "stop" else "running",
            managed=True,
            kind=site.kind,
        )

    def start_site(self, site: HostingSite, tenant_context: TenantContext) -> HostingSiteSummary:
        return self._operate(site, tenant_context, "start")

    def stop_site(self, site: HostingSite, tenant_context: TenantContext) -> HostingSiteSummary:
        return self._operate(site, tenant_context, "stop")

    def restart_site(self, site: HostingSite, tenant_context: TenantContext) -> HostingSiteSummary:
        return self._operate(site, tenant_context, "restart")

    def site_logs(self, site: HostingSite, lines: int) -> SiteLogsResponse:
        self._require_site(site)
        limit = min(max(1, lines), max(1, min(1000, self._settings.log_lines)))
        try:
            logs = Provisioner(self._settings).command(
                "logs", "--tail", str(limit), site.container_name
            )
        except Fallo as exc:
            raise AppError(str(exc), status_code=409, code="hosting_site_unavailable") from exc
        return SiteLogsResponse(logs=logs)

    def delete_site(
        self,
        site: HostingSite,
        tenant_context: TenantContext,
        *,
        app: FastAPI | None = None,
    ) -> None:
        if site.kind == "static":
            self._delete_static(site, tenant_context, app)
            return
        info = self._inspect(site.container_name)
        if info is not None:
            self._managed_site(site)
        names = SiteNames(site.container_name.removeprefix("spanel-").removesuffix("-wp"))
        if names.wp != site.container_name:
            raise AppError(
                "Sitio externo: sin recursos gestionados",
                status_code=409,
                code="hosting_site_not_managed",
            )
        provisioner = Provisioner(self._settings)
        # Never delete a foreign resource even when a row references its name.
        for kind, name in (
            ("container", names.wp),
            ("container", names.db),
            ("volume", names.wp),
            ("volume", names.db),
        ):
            if provisioner.exists(kind, name) and not provisioner._owned(
                kind, name, names, site.tenant_id
            ):
                raise AppError(
                    f"Recurso ajeno: {name}", status_code=409, code="hosting_site_not_managed"
                )
        missing = [
            name
            for kind, name in (
                ("container", names.wp),
                ("container", names.db),
                ("volume", names.wp),
                ("volume", names.db),
            )
            if not provisioner.exists(kind, name)
        ]
        failures = provisioner.cleanup(names, site.tenant_id)
        if failures:
            raise AppError("; ".join(failures), status_code=409, code="hosting_delete_failed")
        # The stable env file holds the site's database secret. A deletion that
        # leaves it behind leaks the secret of a site that no longer exists.
        stable = site_dir(self._settings, site.container_name)
        run(build_docker_argv(f"rm -rf {shlex.quote(stable)}", env=self._env), env=self._env)
        self._audit_site(site, tenant_context, "delete", {"missing": missing})
        self._db.delete(site)

    def _delete_static(
        self,
        site: HostingSite,
        tenant_context: TenantContext,
        app: FastAPI | None,
    ) -> None:
        names = StaticNames(site.container_name.removeprefix("spanel-").removesuffix("-web"))
        if names.container != site.container_name:
            raise AppError(
                "Sitio externo: sin recursos gestionados",
                status_code=409,
                code="hosting_site_not_managed",
            )
        provisioner = Provisioner(self._settings)
        # Reversed by `cleanup`, so the container goes before its volume.
        resources = [("volume", names.volume), ("container", names.container)]
        for kind, name in resources:
            if provisioner.exists(kind, name) and not provisioner._owned(
                kind, name, names, site.tenant_id
            ):
                raise AppError(
                    f"Recurso ajeno: {name}", status_code=409, code="hosting_site_not_managed"
                )
        missing = [name for kind, name in resources if not provisioner.exists(kind, name)]
        publisher = get_site_domain_publisher(app)
        if publisher is not None and site.domain:
            try:
                publisher.unpublish(site.domain)
            except Exception as exc:
                raise AppError(
                    f"No se pudo quitar la ruta publicada: {exc}",
                    status_code=409,
                    code="hosting_delete_failed",
                ) from exc
        failures = provisioner.cleanup(names, site.tenant_id, resources)
        if failures:
            raise AppError("; ".join(failures), status_code=409, code="hosting_delete_failed")
        self._audit_site(
            site, tenant_context, "delete", {"missing": missing, "kind": "static"}
        )
        self._db.delete(site)

    def list_sites(self, tenant_id: str | None) -> HostingSiteListResponse:
        """Rows of one tenant, or all tenants for a platform-wide read view."""
        stmt: Select[tuple[HostingSite]] = select(HostingSite)
        if tenant_id is not None:
            stmt = stmt.where(HostingSite.tenant_id == tenant_id)
        stmt = stmt.order_by(HostingSite.container_name.asc())
        summaries: list[HostingSiteSummary] = []
        for site in self._db.scalars(stmt):
            info = self._inspect(site.container_name)
            if info is None:
                # Visible, marked missing, and not offered as operable: a
                # container that does not exist is not an exception in a list.
                summaries.append(
                    HostingSiteSummary(
                        id=site.id,
                        container_name=site.container_name,
                        domain=site.domain,
                        status="missing",
                        managed=False,
                        kind=site.kind,
                    )
                )
                continue
            summaries.append(
                HostingSiteSummary(
                    id=site.id,
                    container_name=site.container_name,
                    domain=site.domain,
                    status=estado_del_contenedor(info),
                    managed=candidato_de(info).gestionable,
                    kind=site.kind,
                )
            )
        return HostingSiteListResponse(sites=summaries)

    def get_site_config(self, site: HostingSite) -> SiteConfigResponse:
        """The editable env and the NAMES of the secrets. Values never leak."""
        self._require_wordpress(site)
        config = RawAdapter(self._settings).read_config(site)
        return SiteConfigResponse(
            site_id=config.site_id,
            container_name=config.container_name,
            editable_env=config.editable,
            secret_names=list(config.secrets),
            editable_keys=sorted(ENV_WHITELIST),
        )

    def site_database(
        self, site: HostingSite, tenant_context: TenantContext
    ) -> SiteDatabaseResponse:
        """The site's own database credential and the phpMyAdmin link.

        Read-only toward the database: the only side effect is an audit row
        that names the database and the host, never the password.
        """
        self._require_wordpress(site)
        info, _managed = self._require_site(site)
        _raw_host, host, database, user, password = self._database_binding_for(site, info)
        db_info = self._inspect(host)
        if db_info is None:
            raise AppError(
                f"El contenedor {host} no existe o no responde",
                status_code=409,
                code="hosting_database_unavailable",
            )
        if estado_del_contenedor(db_info) != "running":
            raise AppError(
                f"La base de datos {host} no está en marcha",
                status_code=409,
                code="hosting_database_stopped",
            )
        self._audit_site(
            site,
            tenant_context,
            "database_access",
            {"database": database, "host": host},
        )
        return SiteDatabaseResponse(
            site_id=site.id,
            container_name=site.container_name,
            host=host,
            database=database,
            user=user,
            password=password,
            url=phpmyadmin_url(self._settings.phpmyadmin_url, host),
        )

    def _ensure_database_network(self, db_info: dict, host: str, network: str) -> bool:
        """Attach the site's database container to the hub network, once.

        `needs_connection` reads the already fetched `docker inspect`; when the
        interface exists nothing runs. On a real connection the caller audits
        `network`, never a credential.
        """
        if not needs_connection(db_info, network):
            return False
        try:
            argv = connect_args(network, host)
        except ValueError as exc:
            raise AppError(
                "La red de bases de datos no es válida",
                status_code=409,
                code="hosting_phpmyadmin_unavailable",
            ) from exc
        command = "docker " + " ".join(shlex.quote(part) for part in argv)
        try:
            result = run(
                build_docker_argv(command, env=self._env),
                env=self._env,
                timeout=self._settings.command_timeout,
            )
        except (OSError, ValueError) as exc:
            raise AppError(
                "No se pudo preparar la red de la base de datos",
                status_code=409,
                code="hosting_phpmyadmin_unavailable",
            ) from exc
        if result.returncode:
            # The command names containers and a network, never a password.
            raise AppError(
                f"No se pudo conectar {host} a la red {network}",
                status_code=409,
                code="hosting_phpmyadmin_unavailable",
            )
        return True

    def phpmyadmin_login_credentials(
        self, site: HostingSite, tenant_context: TenantContext, client: PmaClient
    ) -> dict[str, str]:
        """Log in upstream here; the HTTP route never receives the DB password."""
        self._require_wordpress(site)
        try:
            info, _ = self._require_site(site)
            _, host, database, user, password = self._database_binding_for(site, info)
        except AppError as exc:
            raise AppError(
                "Sitio o base de datos no disponible",
                status_code=409,
                code="hosting_phpmyadmin_unavailable",
            ) from exc
        if estado_del_contenedor(info) != "running":
            raise AppError(
                "Sitio no disponible", status_code=409, code="hosting_phpmyadmin_unavailable"
            )
        db_info = self._inspect(host)
        if db_info is None or estado_del_contenedor(db_info) != "running":
            raise AppError(
                "Base de datos no disponible",
                status_code=409,
                code="hosting_phpmyadmin_unavailable",
            )
        network = self._settings.phpmyadmin_db_network.strip()
        arbitrary = self._settings.phpmyadmin_arbitrary
        multi = multi_database_enabled(arbitrary, network)
        connected = False
        if multi:
            try:
                validate_network_name(network)
            except ValueError as exc:
                raise AppError(
                    "La red de bases de datos no es válida",
                    status_code=409,
                    code="hosting_phpmyadmin_unavailable",
                ) from exc
            connected = self._ensure_database_network(db_info, host, network)
        # `None` keeps the old fixed-server POST byte-for-byte.
        cookies = client.login(user, password, host if multi else None)
        details = {"database": database, "host": host}
        if connected:
            details["network"] = network
        self._audit_site(site, tenant_context, "phpmyadmin_access", details)
        return cookies

    def update_site_config(
        self,
        site: HostingSite,
        tenant_context: TenantContext,
        env: dict[str, str],
    ) -> SiteConfigResponse:
        """Persist whitelisted env changes and recreate the owned container."""
        self._require_wordpress(site)
        config = RawAdapter(self._settings).update_config(site, env)
        self._audit_site(site, tenant_context, "config_update", {"keys": sorted(env)})
        return SiteConfigResponse(
            site_id=config.site_id,
            container_name=config.container_name,
            editable_env=config.editable,
            secret_names=list(config.secrets),
            editable_keys=sorted(ENV_WHITELIST),
        )

    def rotate_site_salts(
        self, site: HostingSite, tenant_context: TenantContext
    ) -> SecretRotationResponse:
        """Rotate WordPress auth salts. Audited; no secret value is recorded."""
        self._require_wordpress(site)
        RawAdapter(self._settings).rotate_salts(site)
        self._audit_site(site, tenant_context, "secrets_rotated", {"kind": "salts"})
        return SecretRotationResponse(
            site_id=site.id,
            container_name=site.container_name,
            rotated=["salts"],
        )

    def _credentials(self, site: HostingSite, info: dict) -> tuple[dict[str, str], str, str]:
        """Resolve the WordPress DB container, credentials and table prefix.

        The same environment-first, `wp-config.php`-second rule used by the
        database dialog feeds the user list and the password reset. The raw
        host (port included) is kept for the throwaway env file; the container
        name without the port is the one `docker exec` can use.
        """
        raw_host, db_container, database, user, password = self._database_binding_for(site, info)
        try:
            prefijo = prefijo_de_tablas(
                self._env,
                db_container,
                user,
                password,
                database,
            )
        except Fallo as exc:
            raise AppError(
                str(exc),
                status_code=409,
                code="hosting_site_unavailable",
            ) from exc
        credenciales = {
            "host": raw_host,
            "usuario": user,
            "password": password,
            "base": database,
            "_password": password,
            "_contenido": (
                f"WORDPRESS_DB_HOST={raw_host}\n"
                f"WORDPRESS_DB_USER={user}\n"
                f"WORDPRESS_DB_PASSWORD={password}\n"
                f"WORDPRESS_DB_NAME={database}\n"
            ),
        }
        return credenciales, db_container, prefijo

    def list_users(self, site: HostingSite) -> WordPressUserListResponse:
        self._require_wordpress(site)
        info, _managed = self._require_site(site)
        credenciales, db_container, prefijo = self._credentials(site, info)
        try:
            rows = usuarios_con_rol(
                self._env,
                db_container,
                credenciales["usuario"],
                credenciales["_password"],
                credenciales["base"],
                prefijo,
            )
        except Fallo as exc:
            raise AppError(
                f"No se pudieron leer los usuarios de {site.container_name}: {exc}",
                status_code=409,
                code="hosting_site_unavailable",
            ) from exc

        return WordPressUserListResponse(
            site_id=site.id,
            container_name=site.container_name,
            users=[
                WordPressUserSummary(
                    id=int(row["id"]),
                    login=row["login"],
                    email=row["email"],
                    status=estado_de_usuario(row["estado"]),
                    role=rol_mostrado(row["rol"]),
                )
                for row in rows
            ],
        )

    def reset_password(
        self,
        site: HostingSite,
        *,
        wp_user_id: int,
        password: str | None,
        tenant_context: TenantContext,
        correlation_id: str | None = None,
        request_id: str | None = None,
    ) -> ResetPasswordResponse:
        self._require_wordpress(site)
        info, managed = self._require_site(site)
        if not managed:
            # Visible but outside the operations scope. Nothing is written,
            # not even the audit row.
            raise AppError(
                f"El sitio {site.container_name} no es gestionable (spanel.managed=false)",
                status_code=409,
                code="hosting_site_not_managed",
            )

        credenciales, db_container, prefijo = self._credentials(site, info)
        try:
            # The selector is the numeric WordPress ID, never the login: a
            # duplicate login is legal in the schema and must not be touched.
            rows = filas_de_usuarios(
                self._env,
                db_container,
                credenciales["usuario"],
                credenciales["_password"],
                credenciales["base"],
                prefijo,
                f"ID={int(wp_user_id)}",
            )
            if not rows:
                raise NotFoundError(f"User {wp_user_id} not found")
            user = rows[0]

            generated = password is None
            new_password = password or generar_password()
            hash_nuevo = hash_de_password(
                self._env,
                info,
                credenciales,
                db_container,
                new_password,
            )
            actualizar_hash(
                self._env,
                db_container,
                credenciales["usuario"],
                credenciales["_password"],
                credenciales["base"],
                prefijo,
                wp_user_id,
                hash_nuevo,
            )
            read_back = leer_hash(
                self._env,
                db_container,
                credenciales["usuario"],
                credenciales["_password"],
                credenciales["base"],
                prefijo,
                wp_user_id,
            )
        except Fallo as exc:
            raise AppError(
                f"No se pudo restablecer la contraseña en {site.container_name}: {exc}",
                status_code=409,
                code="hosting_password_reset_failed",
            ) from exc

        if read_back != hash_nuevo:
            raise AppError(
                f"La escritura en {site.container_name} no se pudo verificar por ID",
                status_code=500,
                code="hosting_password_readback_failed",
            )

        # Audit carries the real context. The details deliberately describe the
        # operation without the password, the hash, or anything derived from
        # them.
        record_audit(
            self._db,
            tenant_id=tenant_context.current_tenant_id,
            branch_id=tenant_context.current_branch_id,
            actor_user_id=tenant_context.current_user_id,
            actor_type="user",
            module="hosting",
            action="hosting.user.password_reset",
            entity_type="hosting_user",
            entity_id=str(wp_user_id),
            result="success",
            correlation_id=correlation_id,
            request_id=request_id,
            details={
                "site_id": site.id,
                "container_name": site.container_name,
                "wp_user_id": int(wp_user_id),
                "login": user["login"],
                "generated": generated,
            },
        )

        return ResetPasswordResponse(
            site_id=site.id,
            user_id=int(wp_user_id),
            login=user["login"],
            generated=generated,
            password=new_password if generated else None,
        )
