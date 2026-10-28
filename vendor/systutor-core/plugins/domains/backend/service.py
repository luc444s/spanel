"""Casos de uso del plugin de dominios.

El servicio no sabe de HTTP y no escribe YAML a mano: delega en
`ProxyOperations`, la misma implementación que usa el CLI. La propiedad se
decide con el contrato `domain_ownership`, nunca importando Hosting.
"""

from __future__ import annotations

from io import StringIO

from fastapi import FastAPI
from sqlalchemy.orm import Session

from plugins.domains.backend.proxy import (
    EXIT_VALIDATION,
    ProxyError,
    ProxyOperations,
    Settings,
    build_runner,
    normalize_domain,
)
from plugins.domains.backend.schemas import (
    CertificateResponse,
    DomainCheckResponse,
    DomainItem,
    SiteOption,
)
from plugins.domains.backend.settings import get_domains_settings
from systutor.contracts.domain_ownership import (
    is_primary_domain,
    list_sites,
    resolve_site,
)
from systutor.contracts.site_namespace import domain_in_suffix, get_site_suffix
from systutor.core.errors import AppError, NotFoundError
from systutor.kernel.audit.service import record_audit
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.service import list_tenants


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

    # ---- lectura ---------------------------------------------------------

    def list_domains(
        self,
        tenant_id: str,
        *,
        can_manage: bool,
        include_outside: bool = False,
        tenant_name: str | None = None,
    ) -> tuple[list[DomainItem], list[SiteOption]]:
        suffix = get_site_suffix(self._app, self._db, tenant_id)
        sites = self._visible_sites(tenant_id)
        try:
            extras = self._operations().managed_domains()
        except ProxyError as exc:
            raise self._proxy_error(exc) from exc

        def outside_tenant(domain: str) -> bool:
            # Sin sufijo configurado no hay namespace que aplicar.
            return bool(suffix) and not domain_in_suffix(domain, suffix)

        def item(
            domain: str,
            *,
            site: SiteOption,
            owner: str,
            editable: bool,
            conflict: bool,
        ) -> DomainItem | None:
            outside = outside_tenant(domain)
            if outside and not include_outside:
                return None
            return DomainItem(
                domain=domain,
                site_id=site.site_id,
                container=site.container,
                owner=owner,  # type: ignore[arg-type]
                editable=editable,
                conflict=conflict,
                outside_tenant=outside,
                tenant_id=tenant_id,
                tenant_name=tenant_name,
            )

        extras_by_domain = {row.domain.lower(): row for row in extras}
        items: list[DomainItem] = []
        seen: set[str] = set()

        for site in sites.values():
            domain = (site.primary_domain or "").strip().lower()
            if not domain or domain in seen:
                continue
            seen.add(domain)
            # A static site's primary is published by the domains publisher in
            # its own managed YAML: same container, same Host, one writer. Only
            # a YAML owned by a DIFFERENT container is a real second owner.
            extra = extras_by_domain.get(domain)
            row = item(
                domain,
                site=site,
                owner="hosting",
                editable=False,
                conflict=extra is not None and extra.container != site.container,
            )
            if row is not None:
                items.append(row)

        for domain, extra in sorted(extras_by_domain.items()):
            site = sites.get(extra.container)
            if site is None or domain in seen:
                continue
            seen.add(domain)
            row = item(
                domain,
                site=site,
                owner="domains",
                editable=can_manage,
                conflict=False,
            )
            if row is not None:
                items.append(row)

        return items, sorted(sites.values(), key=lambda option: option.container)

    def list_all_domains(self) -> tuple[list[DomainItem], list[SiteOption]]:
        """Vista de plataforma: agrega los dominios de todos los tenants.

        Si el proxy no está disponible para un tenant se omite ese tenant en
        lugar de romper la vista completa.
        """

        items: list[DomainItem] = []
        sites: list[SiteOption] = []
        for tenant in list_tenants(self._db):
            try:
                tenant_items, tenant_sites = self.list_domains(
                    tenant.id,
                    can_manage=False,
                    include_outside=True,
                    tenant_name=tenant.name,
                )
            except AppError:
                continue
            items.extend(tenant_items)
            sites.extend(tenant_sites)
        return items, sites

    def check_domain(self, tenant_context: TenantContext, domain: str) -> DomainCheckResponse:
        """Disponibilidad de un dominio para el tenant actual, sin reservar.

        Valida el formato primero: un dominio mal formado es `422`, nunca un
        `available=false` ambiguo. Después aplica el sufijo y los dueños
        existentes, en ese orden.
        """
        settings = self._settings()
        try:
            needle = normalize_domain(domain, settings.profile)
        except ProxyError as exc:
            raise self._proxy_error(exc) from exc

        suffix = get_site_suffix(
            self._app, self._db, tenant_context.current_tenant_id
        )
        if not suffix:
            return DomainCheckResponse(
                available=False,
                reason="El tenant no tiene sufijo de sitios configurado",
                suffix=None,
            )
        if not domain_in_suffix(needle, suffix):
            return DomainCheckResponse(
                available=False,
                reason=f"{needle} está fuera del sufijo {suffix} del tenant",
                suffix=suffix,
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
                    reason=(
                        f"{needle} ya está gestionado por el contenedor "
                        f"{row.container}"
                    ),
                    suffix=suffix,
                )

        return DomainCheckResponse(available=True, reason=None, suffix=suffix)

    def certificate(
        self, tenant_id: str, domain: str, *, include_outside: bool = False
    ) -> CertificateResponse:
        needle = domain.strip().lower()
        items, _ = self.list_domains(
            tenant_id, can_manage=False, include_outside=include_outside
        )
        if not any(item.domain == needle for item in items):
            raise NotFoundError("Dominio desconocido")
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

    # ---- escritura -------------------------------------------------------

    def add(
        self,
        tenant_context: TenantContext,
        *,
        site_id: str,
        domain: str,
        www: bool,
    ) -> list[DomainItem]:
        tenant_id = tenant_context.current_tenant_id
        site = resolve_site(self._app, self._db, tenant_id, site_id)
        if site is None:
            raise NotFoundError("Sitio desconocido")

        settings = self._settings()
        try:
            primary = normalize_domain(domain, settings.profile)
            candidates = [primary]
            if www:
                candidates.append(normalize_domain(f"www.{primary}", settings.profile))
        except ProxyError as exc:
            raise self._proxy_error(exc) from exc

        for candidate in candidates:
            if is_primary_domain(self._app, self._db, candidate):
                raise AppError(
                    f"{candidate} es el dominio primario de un sitio y lo gestiona Hosting",
                    status_code=409,
                    code="domains.primary_owned",
                )

        operations = self._operations()
        claimed = {
            row.domain.lower(): row for row in operations.managed_domains()
        }
        for candidate in candidates:
            owner = claimed.get(candidate)
            if owner is not None and owner.container != site.container:
                raise AppError(
                    f"{candidate} ya está gestionado por {owner.container}",
                    status_code=409,
                    code="domains.already_claimed",
                )

        operations = self._ready()
        try:
            operations.add(site.container, primary, www=www, wait=0, set_wordpress_url=False)
        except ProxyError as exc:
            raise self._proxy_error(exc) from exc

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

    def remove(self, tenant_context: TenantContext, domain: str) -> None:
        tenant_id = tenant_context.current_tenant_id
        settings = self._settings()
        try:
            needle = normalize_domain(domain, settings.profile)
        except ProxyError as exc:
            raise self._proxy_error(exc) from exc

        operations = self._operations()
        extras = {row.domain.lower(): row for row in operations.managed_domains()}
        claim = extras.get(needle)
        if claim is None:
            if is_primary_domain(self._app, self._db, needle):
                raise AppError(
                    f"{needle} es el dominio primario y lo gestiona Hosting",
                    status_code=409,
                    code="domains.primary_owned",
                )
            raise NotFoundError("Dominio desconocido")

        sites = self._visible_sites(tenant_id)
        if claim.container not in sites:
            raise NotFoundError("Dominio desconocido")
        site = sites[claim.container]
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
                "site_id": sites[claim.container].site_id,
                "container": claim.container,
            },
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
