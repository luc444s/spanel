"""Casos de uso del plugin de dominios.

Delega en `ProxyOperations` (el mismo escritor que usa el CLI) y decide la
propiedad con los contratos `domain_namespace`/`domain_ownership`.
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
