"""Casos de uso del plugin de dominios.

Delega en `ProxyOperations` (el mismo escritor que usa el CLI) y decide la
propiedad con los contratos `domain_namespace`/`domain_ownership`.
"""

from __future__ import annotations

import logging
from io import StringIO

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from plugins.domains.backend.models import DomainRoute
from plugins.domains.backend.proxy import (
    EXIT_VALIDATION,
    PathRoute,
    ProxyError,
    ProxyOperations,
    Settings,
    build_runner,
    normalize_domain,
    normalize_path_prefix,
)
from plugins.domains.backend.schemas import (
    AvailableDomainsResponse,
    CertificateResponse,
    DomainCheckResponse,
    DomainItem,
    RouteCreate,
    RouteItem,
    RouteProbeResponse,
    RouteUpdate,
    SiteOption,
)
from plugins.domains.backend.settings import get_domains_settings
from systutor.contracts.domain_namespace import (
    DomainConflictError,
    DomainUsage,
    claim_domain,
    domain_owner,
    list_tenant_domains,
    release_domain,
)
from systutor.contracts.domain_ownership import (
    is_primary_domain,
    list_sites,
    resolve_site,
)
from systutor.contracts.site_namespace import get_site_suffix
from systutor.core.errors import AppError, NotFoundError
from systutor.kernel.audit.service import record_audit
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Tenant
from systutor.kernel.tenants.service import list_tenants

logger = logging.getLogger(__name__)


def published_domain_usage(db: Session, domain: str) -> DomainUsage | None:
    """Uso extra publicado por el proxy, o `None` si el proxy no responde."""
    try:
        settings = get_domains_settings().proxy_settings()
        operations = ProxyOperations(settings, build_runner(settings))
        for row in operations.managed_domains():
            if row.domain.lower() == domain:
                return DomainUsage(container=row.container, kind="extra")
    except Exception:  # noqa: BLE001 - el proxy no debe romper una lectura
        return None
    return None


def release_application_routes(
    db: Session, app: FastAPI, tenant_id: str, application_id: str
) -> None:
    """Libera las rutas de todos los servicios de una aplicación eliminada.

    Se invoca desde el worker de Apps mediante un hook del runtime; la
    propiedad, el renderer y la escritura siguen siendo de Dominios.
    """
    prefix = f"{application_id}:"
    rows = list(
        db.scalars(
            select(DomainRoute).where(
                DomainRoute.tenant_id == tenant_id,
                DomainRoute.site_id.startswith(prefix),
            )
        ).all()
    )
    if not rows:
        return
    service = DomainsService(db, app)
    domains = sorted({row.domain for row in rows})
    for row in rows:
        db.delete(row)
    db.flush()
    for domain in domains:
        try:
            service._publish_domain_routes(tenant_id, domain, ready=False)
        except Exception:  # noqa: BLE001 - el proxy caído no bloquea el delete
            logger.warning("domains_route_cleanup_failed", extra={"domain": domain})
    db.commit()


def refresh_application_routes(
    db: Session, app: FastAPI, tenant_id: str, application_id: str
) -> None:
    """Re-publish an app's routes after a deployment replaced its containers.

    Only rewrites Domains-owned documents and reconnects the current containers
    to the proxy network. A failing proxy never fails the deployment.
    """
    prefix = f"{application_id}:"
    rows = list(
        db.scalars(
            select(DomainRoute).where(
                DomainRoute.tenant_id == tenant_id,
                DomainRoute.site_id.startswith(prefix),
            )
        ).all()
    )
    if not rows:
        return
    service = DomainsService(db, app)
    for domain in sorted({row.domain for row in rows}):
        try:
            service._publish_domain_routes(tenant_id, domain, ready=True)
        except Exception:  # noqa: BLE001 - el proxy caído no bloquea el deploy
            logger.warning(
                "domains_route_refresh_failed",
                extra={"domain": domain, "application_id": application_id},
                exc_info=True,
            )
    db.commit()


class DomainsService:
    def __init__(self, db: Session, app: FastAPI) -> None:
        self._db = db
        self._app = app

    # ---- infraestructura -------------------------------------------------

    def _settings(self) -> Settings:
        try:
            return get_domains_settings().proxy_settings()
        except ValueError as exc:
            raise AppError(
                "Perfil de proxy inválido", status_code=503, code="domains.proxy_unavailable"
            ) from exc
    def _operations(self) -> ProxyOperations:
        settings = self._settings()
        return ProxyOperations(settings, build_runner(settings), StringIO(), StringIO())

    def _ready(self) -> ProxyOperations:
        operations = self._operations()
        ok, detail = operations.config_writable()
        if not ok:
            raise AppError(
                f"El proxy no está disponible: {detail}",
                status_code=503,
                code="domains.proxy_unavailable",
            )
        return operations

    def _visible_sites(self, tenant_id: str) -> dict[str, SiteOption]:
        return {
            site.container: SiteOption(
                site_id=site.site_id,
                container=site.container,
                primary_domain=site.primary_domain,
            )
            for site in list_sites(self._app, self._db, tenant_id)
        }

    @staticmethod
    def _proxy_error(exc: ProxyError) -> AppError:
        status = 422 if exc.exit_code == EXIT_VALIDATION else 503
        code = "domains.invalid_domain" if status == 422 else "domains.proxy_unavailable"
        return AppError(str(exc), status_code=status, code=code)

    def _candidates(self, domain: str, www: bool) -> tuple[str, list[str]]:
        """Normaliza el dominio y sus candidatos de `www` (422 si es inválido)."""
        settings = self._settings()
        try:
            primary = normalize_domain(domain, settings.profile)
            candidates = [primary]
            if www:
                candidates.append(normalize_domain(f"www.{primary}", settings.profile))
        except ProxyError as exc:
            raise self._proxy_error(exc) from exc
        return primary, candidates

    def _reject_primaries(self, candidates: list[str]) -> None:
        """Un dominio primario de cualquier sitio no se puede reclamar."""
        for candidate in candidates:
            if is_primary_domain(self._app, self._db, candidate):
                raise AppError(
                    f"{candidate} es el dominio primario de un sitio y lo gestiona Hosting",
                    status_code=409,
                    code="domains.primary_owned",
                )

    def _claim_free(
        self, tenant_id: str, candidates: list[str]
    ) -> list[str]:
        """Verifica propiedad global y reclama los libres (otro tenant → 409)."""
        free: list[str] = []
        for candidate in candidates:
            owner_tenant = domain_owner(self._app, self._db, candidate)
            if owner_tenant is not None and owner_tenant != tenant_id:
                raise AppError(
                    f"{candidate} lo administra otro tenant",
                    status_code=409,
                    code="domains.already_owned",
                )
            if owner_tenant is None:
                free.append(candidate)
        for candidate in free:
            try:
                claim_domain(self._app, self._db, tenant_id, candidate)
            except DomainConflictError as exc:
                raise AppError(
                    f"{candidate} lo administra otro tenant",
                    status_code=409,
                    code="domains.already_owned",
                ) from exc
        return free

    def _publish(
        self,
        operations: ProxyOperations,
        site: SiteOption,
        primary: str,
        www: bool,
    ) -> None:
        try:
            operations.add(site.container, primary, www=www, wait=0, set_wordpress_url=False)
        except ProxyError as exc:
            rollback = getattr(self._db, "rollback", None)
            if rollback is not None:
                rollback()
            raise self._proxy_error(exc) from exc

    def _extra_claims(self) -> dict[str, object]:
        return {row.domain.lower(): row for row in self._operations().managed_domains()}

    # ---- lectura ---------------------------------------------------------

    def _reserved_domains(self, tenant_id: str, *, seen: set[str]) -> list[str]:
        """Dominios propios del tenant que no están publicados.

        Excluye `tenants.domain` y nunca rompe una lectura.
        """
        try:
            owned = list_tenant_domains(self._app, self._db, tenant_id)
            try:
                tenant = self._db.get(Tenant, tenant_id)
            except Exception:  # noqa: BLE001
                tenant = None
            if tenant is not None:
                primary = (tenant.domain or "").strip().lower().removesuffix(".")
                if primary:
                    owned = owned - {primary}
            return sorted(owned - seen)
        except Exception:  # noqa: BLE001 - el inventario no debe romper un listado
            return []

    def list_domains(
        self,
        tenant_id: str,
        *,
        can_manage: bool,
        tenant_name: str | None = None,
    ) -> tuple[list[DomainItem], list[SiteOption], str | None]:
        """Primarios, extras y reservados del tenant (con aviso de proxy)."""
        sites = self._visible_sites(tenant_id)
        proxy_error: str | None = None
        try:
            extras = self._extra_claims()
        except ProxyError as exc:
            proxy_error = str(exc)
            extras = {}

        def item(
            domain: str,
            *,
            site: SiteOption | None = None,
            owner: str,
            editable: bool,
            conflict: bool = False,
            published: bool = True,
        ) -> DomainItem:
            return DomainItem(
                domain=domain,
                site_id=site.site_id if site else None,
                container=site.container if site else None,
                owner=owner,  # type: ignore[arg-type]
                editable=editable,
                conflict=conflict,
                published=published,
                tenant_id=tenant_id,
                tenant_name=tenant_name,
            )

        items: list[DomainItem] = []
        seen: set[str] = set()

        for site in sites.values():
            domain = (site.primary_domain or "").strip().lower()
            if not domain or domain in seen:
                continue
            seen.add(domain)
            # One writer: the primary published by its own container is not a
            # conflict; only a YAML owned by a different container is.
            extra = extras.get(domain)
            items.append(
                item(
                    domain,
                    site=site,
                    owner="hosting",
                    editable=False,
                    conflict=extra is not None and extra.container != site.container,
                )
            )

        for domain, extra in sorted(extras.items()):
            site = sites.get(extra.container)  # type: ignore[union-attr]
            if site is None or domain in seen:
                continue
            seen.add(domain)
            items.append(item(domain, site=site, owner="domains", editable=can_manage))

        # Las rutas se publican en `spanel-route-*.yml`, que no forman parte de
        # `extras`. Sin este cruce, un dominio activo de Apps aparecía como
        # reservado ("Sin publicar") y ofrecía vincularlo otra vez.
        for row in self._route_rows(tenant_id):
            domain = row.domain.strip().lower()
            if not domain or domain in seen:
                continue
            site = self._route_site(tenant_id, row.site_id)
            if site is None:
                continue
            seen.add(domain)
            items.append(item(domain, site=site, owner="domains", editable=can_manage))

        for domain in self._reserved_domains(tenant_id, seen=seen):
            seen.add(domain)
            items.append(
                item(domain, owner="domains", editable=can_manage, published=False)
            )

        return items, sorted(sites.values(), key=lambda option: option.container), proxy_error

    def list_all_domains(
        self,
    ) -> tuple[list[DomainItem], list[SiteOption], str | None]:
        """Vista de plataforma: agrega los dominios de todos los tenants."""
        items: list[DomainItem] = []
        sites: list[SiteOption] = []
        proxy_error: str | None = None
        for tenant in list_tenants(self._db):
            try:
                tenant_items, tenant_sites, tenant_error = self.list_domains(
                    tenant.id,
                    can_manage=False,
                    tenant_name=tenant.name,
                )
            except AppError:
                continue
            items.extend(tenant_items)
            sites.extend(tenant_sites)
            if tenant_error is not None and proxy_error is None:
                proxy_error = tenant_error
        return items, sites, proxy_error

    def check_domain(self, tenant_context: TenantContext, domain: str) -> DomainCheckResponse:
        """Disponibilidad de un dominio sin reservar: formato, uso y propiedad."""
        try:
            needle = normalize_domain(domain, self._settings().profile)
        except ProxyError as exc:
            raise self._proxy_error(exc) from exc

        suffix = get_site_suffix(
            self._app, self._db, tenant_context.current_tenant_id
        )

        for site in list_sites(self._app, self._db, None):  # type: ignore[arg-type]
            if (site.primary_domain or "").strip().lower() == needle:
                return DomainCheckResponse(
                    available=False,
                    reason=(
                        f"{needle} es el dominio primario del contenedor "
                        f"{site.container} y lo gestiona Hosting"
                    ),
                    suffix=suffix,
                )

        try:
            extras = self._operations().managed_domains()
        except ProxyError as exc:
            raise self._proxy_error(exc) from exc
        for row in extras:
            if row.domain.lower() == needle:
                return DomainCheckResponse(
                    available=False,
                    reason=f"{needle} ya está gestionado por el contenedor {row.container}",
                    suffix=suffix,
                )

        owner = domain_owner(self._app, self._db, needle)
        if owner is not None and owner != tenant_context.current_tenant_id:
            return DomainCheckResponse(
                available=False,
                reason=f"{needle} lo administra otro tenant",
                suffix=suffix,
            )

        return DomainCheckResponse(available=True, reason=None, suffix=suffix)

    def certificate(self, tenant_id: str, domain: str) -> CertificateResponse:
        needle = domain.strip().lower()
        items, _, _ = self.list_domains(tenant_id, can_manage=False)
        match = next((item for item in items if item.domain == needle), None)
        if match is None:
            raise NotFoundError("Dominio desconocido")
        if not match.published:
            # Reservado sin ruta: no se toca la red.
            return CertificateResponse(domain=needle, state="unpublished", verified=False)
        try:
            info = self._operations().certificate(needle)
        except ProxyError as exc:
            raise self._proxy_error(exc) from exc
        return CertificateResponse(
            domain=needle,
            state=info.state,
            issuer=info.issuer,
            subject=info.subject,
            not_after=info.not_after,
            sans=list(info.sans),
            verified=info.verified,
            error=info.error,
        )

    # ---- rutas gestionadas (A.SPEC 0060) ---------------------------------

    def _route_rows(
        self,
        tenant_id: str,
        *,
        domain: str | None = None,
        site_id: str | None = None,
    ) -> list[DomainRoute]:
        stmt = select(DomainRoute).where(DomainRoute.tenant_id == tenant_id)
        if domain is not None:
            stmt = stmt.where(DomainRoute.domain == domain)
        if site_id is not None:
            stmt = stmt.where(DomainRoute.site_id == site_id)
        return list(
            self._db.scalars(
                stmt.order_by(DomainRoute.domain, DomainRoute.path, DomainRoute.id)
            ).all()
        )

    def _route_site(self, tenant_id: str, site_id: str) -> SiteOption | None:
        site = resolve_site(self._app, self._db, tenant_id, site_id)
        if site is None:
            return None
        return SiteOption(
            site_id=site.site_id,
            container=site.container,
            primary_domain=site.primary_domain,
        )

    @staticmethod
    def _route_item(row: DomainRoute, available: bool) -> RouteItem:
        return RouteItem(
            id=row.id,
            domain=row.domain,
            path=row.path,
            internal_path=row.internal_path,
            strip_path=row.strip_path,
            port=row.port,
            entrypoints=list(row.entrypoints or []),
            https=row.https,
            middlewares=list(row.middlewares or []),
            site_id=row.site_id,
            available=available,
        )

    def _publish_domain_routes(
        self,
        tenant_id: str,
        domain: str,
        *,
        ready: bool,
    ) -> None:
        rows = self._route_rows(tenant_id, domain=domain)
        routes: list[PathRoute] = []
        for row in rows:
            site = self._route_site(tenant_id, row.site_id)
            if site is None:
                continue
            routes.append(
                PathRoute(
                    prefix=row.path,
                    container=site.container,
                    port=row.port,
                    internal_path=row.internal_path,
                    strip_path=row.strip_path,
                    entrypoints=tuple(row.entrypoints or ()),
                    https=row.https,
                    middlewares=tuple(row.middlewares or ()),
                    route_id=row.id,
                )
            )
        operations = self._ready() if ready else self._operations()
        try:
            operations.write_route_document(
                domain,
                routes,
                validate_runtime=ready,
                connect_network=ready,
            )
        except ProxyError as exc:
            raise self._proxy_error(exc) from exc

    def _registered_domains(self, tenant_id: str) -> set[str]:
        owned = set(list_tenant_domains(self._app, self._db, tenant_id))
        items, _, _ = self.list_domains(tenant_id, can_manage=False)
        for item in items:
            if item.owner == "domains":
                owned.add(item.domain)
        return owned

    @staticmethod
    def _unique_names(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))

    def _route_options(self) -> tuple[list[str], list[str]]:
        settings = self._settings()
        entrypoints = self._unique_names(
            [settings.entrypoint_http, settings.entrypoint_https]
        )
        middlewares = self._unique_names(
            list(get_domains_settings().middlewares)
        )
        return entrypoints, middlewares

    def _validate_route_options(
        self,
        entrypoints: list[str] | None,
        middlewares: list[str] | None,
    ) -> None:
        allowed_entrypoints, allowed_middlewares = self._route_options()
        if entrypoints is not None and any(
            value not in allowed_entrypoints for value in entrypoints
        ):
            raise AppError(
                "Entrypoint no permitido para el perfil efectivo",
                status_code=422,
                code="domains.entrypoint_not_allowed",
            )
        if middlewares is not None and any(
            value not in allowed_middlewares for value in middlewares
        ):
            raise AppError(
                "Middleware no permitido por la plataforma",
                status_code=422,
                code="domains.middleware_not_allowed",
            )

    def available_domains(self, tenant_id: str) -> AvailableDomainsResponse:
        items, _, _ = self.list_domains(tenant_id, can_manage=True)
        entrypoints, middlewares = self._route_options()
        return AvailableDomainsResponse(
            items=[item for item in items if item.owner == "domains"],
            entrypoints=entrypoints,
            middlewares=middlewares,
        )

    def _route_domain(self, tenant_context: TenantContext, domain: str) -> str:
        primary, _ = self._candidates(domain, False)
        if primary not in self._registered_domains(tenant_context.current_tenant_id):
            raise NotFoundError("Dominio desconocido")
        return primary

    def _conflicting_route(
        self, tenant_id: str, domain: str, path: str, *, exclude_id: str | None = None
    ) -> bool:
        stmt = select(DomainRoute.id).where(
            DomainRoute.domain == domain, DomainRoute.path == path
        )
        if exclude_id is not None:
            stmt = stmt.where(DomainRoute.id != exclude_id)
        if self._db.scalar(stmt) is not None:
            return True
        try:
            return self._operations().path_conflict(domain, path)
        except ProxyError:
            return False

    def list_routes(
        self,
        tenant_context: TenantContext,
        *,
        site_id: str | None = None,
        domain: str | None = None,
    ) -> list[RouteItem]:
        tenant_id = tenant_context.current_tenant_id
        if domain is not None:
            domain = self._candidates(domain, False)[0]
        rows = self._route_rows(tenant_id, domain=domain, site_id=site_id)
        domains = sorted({row.domain for row in rows})
        for item in domains:
            try:
                self._publish_domain_routes(tenant_id, item, ready=False)
            except AppError:
                # Una lectura no debe ocultar el inventario por un proxy caído.
                continue
        items: list[RouteItem] = []
        for row in rows:
            items.append(
                self._route_item(
                    row, self._route_site(tenant_id, row.site_id) is not None
                )
            )
        return items

    def get_route(
        self, tenant_context: TenantContext, route_id: str
    ) -> RouteItem:
        row = self._db.scalar(
            select(DomainRoute).where(
                DomainRoute.id == route_id,
                DomainRoute.tenant_id == tenant_context.current_tenant_id,
            )
        )
        if row is None:
            raise NotFoundError("Ruta desconocida")
        try:
            self._publish_domain_routes(row.tenant_id, row.domain, ready=False)
        except AppError:
            pass
        return self._route_item(
            row, self._route_site(row.tenant_id, row.site_id) is not None
        )

    def create_route(
        self, tenant_context: TenantContext, body: RouteCreate
    ) -> RouteItem:
        tenant_id = tenant_context.current_tenant_id
        domain = self._route_domain(tenant_context, body.domain)
        site = self._route_site(tenant_id, body.site_id)
        if site is None:
            raise NotFoundError("Servicio desconocido")
        path = normalize_path_prefix(body.path)
        self._validate_route_options(body.entrypoints, body.middlewares)
        if self._conflicting_route(tenant_id, domain, path):
            raise AppError(
                "La ruta ya está asignada",
                status_code=409,
                code="domains.route_conflict",
            )
        row = DomainRoute(
            tenant_id=tenant_id,
            domain=domain,
            path=path,
            internal_path=normalize_path_prefix(body.internal_path),
            strip_path=body.strip_path,
            port=body.port,
            entrypoints=list(body.entrypoints),
            https=body.https,
            middlewares=list(body.middlewares),
            site_id=site.site_id,
        )
        self._db.add(row)
        self._db.flush()
        try:
            self._publish_domain_routes(tenant_id, domain, ready=True)
        except Exception:
            rollback = getattr(self._db, "rollback", None)
            if rollback is not None:
                rollback()
            raise
        self._audit(
            tenant_context,
            action="domains.route.created",
            domain=domain,
            details={
                "route_id": row.id,
                "site_id": row.site_id,
                "path": row.path,
                "https": row.https,
            },
        )
        return self._route_item(row, True)

    def update_route(
        self, tenant_context: TenantContext, route_id: str, body: RouteUpdate
    ) -> RouteItem:
        tenant_id = tenant_context.current_tenant_id
        row = self._db.scalar(
            select(DomainRoute).where(
                DomainRoute.id == route_id, DomainRoute.tenant_id == tenant_id
            )
        )
        if row is None:
            raise NotFoundError("Ruta desconocida")
        previous_domain = row.domain
        values = body.model_dump(exclude_unset=True)
        if "domain" in values:
            values["domain"] = self._route_domain(tenant_context, values["domain"])
        if "site_id" in values and self._route_site(tenant_id, values["site_id"]) is None:
            raise NotFoundError("Servicio desconocido")
        if "path" in values:
            values["path"] = normalize_path_prefix(values["path"])
        if "internal_path" in values:
            values["internal_path"] = normalize_path_prefix(values["internal_path"])
        self._validate_route_options(
            values.get("entrypoints"), values.get("middlewares")
        )
        target_domain = values.get("domain", row.domain)
        target_path = values.get("path", row.path)
        if self._conflicting_route(
            tenant_id, target_domain, target_path, exclude_id=row.id
        ):
            raise AppError(
                "La ruta ya está asignada",
                status_code=409,
                code="domains.route_conflict",
            )
        for key, value in values.items():
            setattr(row, key, value)
        self._db.flush()
        try:
            self._publish_domain_routes(tenant_id, previous_domain, ready=True)
            if row.domain != previous_domain:
                self._publish_domain_routes(tenant_id, row.domain, ready=True)
        except Exception:
            rollback = getattr(self._db, "rollback", None)
            if rollback is not None:
                rollback()
            raise
        self._audit(
            tenant_context,
            action="domains.route.updated",
            domain=row.domain,
            details={
                "route_id": row.id,
                "site_id": row.site_id,
                "path": row.path,
                "https": row.https,
            },
        )
        return self._route_item(row, True)

    def delete_route(self, tenant_context: TenantContext, route_id: str) -> None:
        tenant_id = tenant_context.current_tenant_id
        row = self._db.scalar(
            select(DomainRoute).where(
                DomainRoute.id == route_id, DomainRoute.tenant_id == tenant_id
            )
        )
        if row is None:
            raise NotFoundError("Ruta desconocida")
        domain = row.domain
        self._db.delete(row)
        self._db.flush()
        try:
            self._publish_domain_routes(tenant_id, domain, ready=True)
        except Exception:
            rollback = getattr(self._db, "rollback", None)
            if rollback is not None:
                rollback()
            raise
        self._audit(
            tenant_context,
            action="domains.route.removed",
            domain=domain,
            details={"route_id": route_id, "site_id": row.site_id, "path": row.path},
        )

    def probe_route(
        self, tenant_context: TenantContext, route_id: str
    ) -> RouteProbeResponse:
        tenant_id = tenant_context.current_tenant_id
        row = self._db.scalar(
            select(DomainRoute).where(
                DomainRoute.id == route_id, DomainRoute.tenant_id == tenant_id
            )
        )
        if row is None:
            raise NotFoundError("Ruta desconocida")
        if self._route_site(tenant_id, row.site_id) is None:
            raise AppError(
                "El servicio de la ruta no está disponible",
                status_code=409,
                code="domains.route_target_unavailable",
            )
        try:
            self._publish_domain_routes(tenant_id, row.domain, ready=False)
            operations = self._operations()
            info, code = operations.probe_route(row.domain, row.path)
        except ProxyError as exc:
            raise self._proxy_error(exc) from exc
        return RouteProbeResponse(
            route_id=row.id,
            domain=row.domain,
            path=row.path,
            http_status=code,
            certificate=CertificateResponse(
                domain=row.domain,
                state=info.state,
                issuer=info.issuer,
                subject=info.subject,
                not_after=info.not_after,
                sans=list(info.sans),
                verified=info.verified,
                error=info.error,
            ),
        )

    # ---- escritura -------------------------------------------------------

    def add(
        self, tenant_context: TenantContext, *, site_id: str | None, domain: str, www: bool
    ) -> list[DomainItem]:
        """Alta de dominio: reserva sin sitio (`site_id=None`) o publicación."""
        tenant_id = tenant_context.current_tenant_id
        primary, candidates = self._candidates(domain, www)

        if site_id is None:
            return self._reserve(tenant_context, primary, candidates, www)

        site = resolve_site(self._app, self._db, tenant_id, site_id)
        if site is None:
            raise NotFoundError("Sitio desconocido")

        self._reject_primaries(candidates)

        claimed = self._extra_claims()
        for candidate in candidates:
            owner = claimed.get(candidate)
            if owner is not None and owner.container != site.container:  # type: ignore[union-attr]
                raise AppError(
                    f"{candidate} ya está gestionado por {owner.container}",  # type: ignore[union-attr]
                    status_code=409,
                    code="domains.already_claimed",
                )

        self._claim_free(tenant_id, candidates)

        operations = self._ready()
        self._publish(operations, site, primary, www)

        self._audit(
            tenant_context,
            action="domains.domain.added",
            domain=primary,
            details={"site_id": site.site_id, "container": site.container, "www": www},
        )
        return [
            DomainItem(
                domain=candidate,
                site_id=site.site_id,
                container=site.container,
                owner="domains",
                editable=True,
                conflict=False,
            )
            for candidate in candidates
        ]

    def _reserve(
        self,
        tenant_context: TenantContext,
        primary: str,
        candidates: list[str],
        www: bool,
    ) -> list[DomainItem]:
        """Reserva solo propiedad en `tenant_domains`; nunca toca el proxy."""
        tenant_id = tenant_context.current_tenant_id

        self._reject_primaries(candidates)

        try:
            claimed = self._extra_claims()
        except ProxyError:
            pass
        else:
            for candidate in candidates:
                owner = claimed.get(candidate)
                if owner is not None:
                    raise AppError(
                        f"{candidate} ya está gestionado por {owner.container}",  # type: ignore[union-attr]
                        status_code=409,
                        code="domains.already_claimed",
                    )

        self._claim_free(tenant_id, candidates)

        self._audit(
            tenant_context,
            action="domains.domain.added",
            domain=primary,
            details={"site_id": None, "container": None, "www": www, "mode": "reserved"},
        )
        return [
            DomainItem(
                domain=candidate,
                owner="domains",
                editable=True,
                conflict=False,
                published=False,
            )
            for candidate in candidates
        ]

    def link(
        self, tenant_context: TenantContext, *, domain: str, site_id: str, www: bool
    ) -> list[DomainItem]:
        """Publica un reservado con el mismo `add()` que el alta con sitio."""
        tenant_id = tenant_context.current_tenant_id
        primary, candidates = self._candidates(domain, www)

        owner_tenant = domain_owner(self._app, self._db, primary)
        if owner_tenant != tenant_id:
            raise AppError(
                f"{primary} no está reservado por este tenant",
                status_code=404,
                code="domains.domain_not_found",
            )

        site = resolve_site(self._app, self._db, tenant_id, site_id)
        if site is None:
            raise AppError(
                "Sitio desconocido",
                status_code=404,
                code="domains.site_not_found",
            )

        self._reject_primaries(candidates)

        claimed = self._extra_claims()
        for candidate in candidates:
            owner = claimed.get(candidate)
            if owner is not None and owner.container != site.container:  # type: ignore[union-attr]
                raise AppError(
                    f"{candidate} ya está gestionado por {owner.container}",  # type: ignore[union-attr]
                    status_code=409,
                    code="domains.already_claimed",
                )

        self._claim_free(tenant_id, candidates)

        operations = self._ready()
        self._publish(operations, site, primary, www)

        self._audit(
            tenant_context,
            action="domains.domain.linked",
            domain=primary,
            details={"site_id": site.site_id, "container": site.container, "www": www},
        )
        return [
            DomainItem(
                domain=candidate,
                site_id=site.site_id,
                container=site.container,
                owner="domains",
                editable=True,
                conflict=False,
            )
            for candidate in candidates
        ]

    def remove(self, tenant_context: TenantContext, domain: str) -> None:
        """Quita una ruta extra, o libera una reserva si no está publicada."""
        tenant_id = tenant_context.current_tenant_id
        needle, _ = self._candidates(domain, False)

        try:
            claim = self._extra_claims().get(needle)
        except ProxyError as exc:
            raise self._proxy_error(exc) from exc
        if claim is None:
            self._remove_reserved(tenant_context, needle)
            return

        sites = self._visible_sites(tenant_id)
        if claim.container not in sites:  # type: ignore[union-attr]
            raise NotFoundError("Dominio desconocido")
        site = sites[claim.container]  # type: ignore[index]
        if (site.primary_domain or "").strip().lower() == needle:
            raise AppError(
                f"{needle} es el dominio primario de {claim.container} y lo publica Hosting",
                status_code=409,
                code="domains.primary_owned",
            )

        operations = self._ready()
        try:
            operations.remove(needle)
        except ProxyError as exc:
            raise self._proxy_error(exc) from exc

        self._audit(
            tenant_context,
            action="domains.domain.removed",
            domain=needle,
            details={
                "site_id": site.site_id,
                "container": claim.container,  # type: ignore[union-attr]
            },
        )

    def _remove_reserved(self, tenant_context: TenantContext, needle: str) -> None:
        """DELETE de un dominio no publicado: libera la fila o responde 404."""
        if is_primary_domain(self._app, self._db, needle):
            raise AppError(
                f"{needle} es el dominio primario y lo gestiona Hosting",
                status_code=409,
                code="domains.primary_owned",
            )
        owner_tenant = domain_owner(self._app, self._db, needle)
        if owner_tenant is None or owner_tenant != tenant_context.current_tenant_id:
            raise NotFoundError("Dominio desconocido")
        if not release_domain(
            self._app, self._db, tenant_context.current_tenant_id, needle
        ):
            raise NotFoundError("Dominio desconocido")
        self._audit(
            tenant_context,
            action="domains.domain.removed",
            domain=needle,
            details={"site_id": None, "container": None, "mode": "reserved"},
        )

    # ---- auditoría -------------------------------------------------------

    def _audit(
        self,
        tenant_context: TenantContext,
        *,
        action: str,
        domain: str,
        details: dict[str, object],
    ) -> None:
        record_audit(
            self._db,
            tenant_id=tenant_context.current_tenant_id,
            branch_id=tenant_context.current_branch_id,
            actor_user_id=tenant_context.current_user_id,
            actor_type="user",
            module="domains",
            action=action,
            entity_type="domain",
            entity_id=domain,
            result="success",
            correlation_id=None,
            request_id=None,
            details=details,
        )
        self._db.commit()
