"""Tenant-scoped site listing, WordPress users and one-password reset.

The router never builds SQL or Docker arguments. This module owns the
`hosting_sites` query, and the only site it ever operates on is the row that the
guard resolved for the authenticated tenant.

A password is generated, hashed with WordPress' own function, written to one
row by ID, read back by ID, and returned once. It is never stored by this
plugin and never written to the audit row or a log line.
"""

from __future__ import annotations

import secrets

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from systutor.core.errors import AppError, NotFoundError
from systutor.kernel.audit.service import record_audit
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Tenant

from .database import (
    conexion_de_la_base,
    filas_de_usuarios,
    prefijo_de_tablas,
    roles_desde_capacidades,
    usuarios_con_rol,
)
from .inspection import candidato_de, database_container, inspect_container
from .models import HostingSite
from .passwords import hash_de_password
from .provisioner import Provisioner, SiteNames, slug_for
from .reset import actualizar_hash, leer_hash
from .schemas import (
    CreateSiteResponse,
    HostingSiteListResponse,
    HostingSiteSummary,
    ResetPasswordResponse,
    SiteLogsResponse,
    WordPressUserListResponse,
    WordPressUserSummary,
)
from .settings import HostingSettings, docker_env, get_hosting_settings
from .transport import Fallo

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
        """The container must exist and still be a WordPress site.

        Returns the inspect payload and whether its `spanel.managed` label
        allows operations. A missing container is 409 and names it.
        """
        info = self._inspect(site.container_name)
        if info is None:
            raise AppError(
                f"El contenedor {site.container_name} no existe o no responde",
                status_code=409,
                code="hosting_site_unavailable",
            )
        candidato = candidato_de(info)
        if not candidato.es_sitio:
            raise AppError(
                f"El contenedor {site.container_name} ya no es un sitio WordPress",
                status_code=409,
                code="hosting_site_unavailable",
            )
        return info, candidato.gestionable

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
        self, tenant_context: TenantContext, name: str, domain: str, admin_email: str
    ) -> CreateSiteResponse:
        try:
            names = SiteNames(slug_for(name))
        except ValueError as exc:
            raise AppError(str(exc), status_code=422, code="hosting_invalid_name") from exc
        tenant_id = tenant_context.current_tenant_id
        tenant = self._db.get(Tenant, tenant_id)
        if tenant is None or not tenant.is_active:
            raise AppError(
                "Tenant inactivo o inexistente", status_code=409, code="hosting_tenant_inactive"
            )
        if self._db.scalar(
            select(HostingSite.id).where(
                (HostingSite.domain == domain) | (HostingSite.container_name == names.wp)
            )
        ):
            raise AppError(
                "Dominio o nombre ya registrado", status_code=409, code="hosting_site_conflict"
            )
        provisioner = Provisioner(self._settings)
        try:
            password = provisioner.create(names, tenant_id, domain, admin_email)
        except Fallo as exc:
            raise AppError(str(exc), status_code=409, code="hosting_provision_failed") from exc
        site = HostingSite(tenant_id=tenant_id, container_name=names.wp, domain=domain)
        try:
            self._db.add(site)
            self._db.flush()
            self._audit_site(site, tenant_context, "create")
            self._db.commit()
        except Exception as exc:
            self._db.rollback()
            failures = provisioner.cleanup(names, tenant_id)
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

    def delete_site(self, site: HostingSite, tenant_context: TenantContext) -> None:
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
        self._audit_site(site, tenant_context, "delete", {"missing": missing})
        self._db.delete(site)

    def list_sites(self, tenant_id: str) -> HostingSiteListResponse:
        """Only the rows owned by the tenant; Docker is asked about those alone."""
        stmt: Select[tuple[HostingSite]] = (
            select(HostingSite)
            .where(HostingSite.tenant_id == tenant_id)
            .order_by(HostingSite.container_name.asc())
        )
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
                )
            )
        return HostingSiteListResponse(sites=summaries)

    def _credentials(self, info: dict) -> tuple[dict[str, str], str, str]:
        """Resolve the WordPress DB container, credentials and table prefix."""
        try:
            credenciales = conexion_de_la_base(info)
            db_container = database_container(info)
            prefijo = prefijo_de_tablas(
                self._env,
                db_container,
                credenciales["usuario"],
                credenciales["_password"],
                credenciales["base"],
            )
        except Fallo as exc:
            raise AppError(
                str(exc),
                status_code=409,
                code="hosting_site_unavailable",
            ) from exc
        return credenciales, db_container, prefijo

    def list_users(self, site: HostingSite) -> WordPressUserListResponse:
        info, _managed = self._require_site(site)
        credenciales, db_container, prefijo = self._credentials(info)
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
        info, managed = self._require_site(site)
        if not managed:
            # Visible but outside the operations scope. Nothing is written,
            # not even the audit row.
            raise AppError(
                f"El sitio {site.container_name} no es gestionable (spanel.managed=false)",
                status_code=409,
                code="hosting_site_not_managed",
            )

        credenciales, db_container, prefijo = self._credentials(info)
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
