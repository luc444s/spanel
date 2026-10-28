"""Casos de uso del gestor de certificados de correo.

La API nunca emite ni instala: filtra el inventario por tenant, escribe
solicitudes validadas y lee el resultado que publica el agente.
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.orm import Session

from plugins.domains.backend.certificates.config import CertificatesConfig
from plugins.domains.backend.certificates.health import (
    alert_view,
    health_view,
    load_alerts,
)
from plugins.domains.backend.certificates.inventory import Inventory, load_inventory
from plugins.domains.backend.certificates.jobs import ActiveJobError, JobStore
from plugins.domains.backend.certificates.models import Certificate
from plugins.domains.backend.certificates.validation import (
    CertificateValidationError,
    normalize_hostname,
)
from plugins.domains.backend.schemas import (
    CertificateAlertsResponse,
    CertificateHealthResponse,
    CertificateItem,
    CertificateJobResponse,
    CertificateRefreshResponse,
    CertificateRequestResponse,
    CertificatesResponse,
)
from systutor.contracts.domain_ownership import list_sites
from systutor.core.errors import AppError, NotFoundError
from systutor.kernel.audit.service import record_audit
from systutor.kernel.tenants.context import TenantContext


class CertificatesService:
    def __init__(
        self,
        db: Session,
        app: FastAPI,
        *,
        config: CertificatesConfig | None = None,
    ) -> None:
        self._db = db
        self._app = app
        configured = getattr(app.state, "certificates_config", None)
        self._config = config or configured or CertificatesConfig.from_env()
        self._jobs = JobStore(self._config)
        self._load_inventory = load_inventory

    @property
    def config(self) -> CertificatesConfig:
        return self._config

    # ---- visibilidad -----------------------------------------------------

    def _tenant_domains(self, tenant_id: str) -> set[str]:
        domains: set[str] = set()
        try:
            sites = list_sites(self._app, self._db, tenant_id)
        except Exception:  # noqa: BLE001 - un tenant sin proveedor no ve nada
            sites = []
        domains.update(
            (site.primary_domain or "").strip().lower()
            for site in sites
            if (site.primary_domain or "").strip()
        )
        # Los dominios extra también pertenecen al tenant: si se gestionan en
        # Dominios, su certificado mail.<dominio> debe ser visible allí. El
        # proxy puede no estar disponible (p. ej. en tests); en ese caso se
        # conserva la visibilidad por dominios primarios.
        try:
            from plugins.domains.backend.service import DomainsService

            items, _sites = DomainsService(self._db, self._app).list_domains(
                tenant_id, can_manage=False
            )
            domains.update(item.domain.strip().lower() for item in items)
        except Exception:  # noqa: BLE001 - proxy ausente: solo primarios
            pass
        return domains

    def _inventory(self) -> Inventory:
        return self._load_inventory(self._config)

    def _visible(self, inventory: Inventory, tenant_context: TenantContext) -> list[Certificate]:
        domains = self._tenant_domains(tenant_context.current_tenant_id)
        return [
            cert
            for cert in inventory.certificates
            if cert.visible_to(
                tenant_id=tenant_context.current_tenant_id,
                is_platform=tenant_context.is_platform,
                tenant_domains=domains,
            )
        ]

    def _visible_certificate(
        self, tenant_context: TenantContext, hostname: str
    ) -> Certificate:
        try:
            needle = normalize_hostname(hostname)
        except CertificateValidationError as exc:
            raise AppError(
                str(exc), status_code=422, code="domains.certificates_invalid_hostname"
            ) from exc
        inventory = self._inventory()
        certificate = inventory.find(needle)
        if certificate is None:
            raise NotFoundError("Certificado desconocido")
        domains = self._tenant_domains(tenant_context.current_tenant_id)
        if not certificate.visible_to(
            tenant_id=tenant_context.current_tenant_id,
            is_platform=tenant_context.is_platform,
            tenant_domains=domains,
        ):
            # 404 para no filtrar la existencia de hostnames de otros tenants.
            raise NotFoundError("Certificado desconocido")
        return certificate

    # ---- lectura ---------------------------------------------------------

    def list_certificates(self, tenant_context: TenantContext) -> CertificatesResponse:
        inventory = self._inventory()
        items = [
            CertificateItem(**cert.to_dict())
            for cert in self._visible(inventory, tenant_context)
        ]
        return CertificatesResponse(
            items=items,
            stale=inventory.stale,
            generated_at=inventory.generated_at,
            agent_last_seen=inventory.agent_last_seen,
            error=inventory.error,
        )

    def get_certificate(
        self, tenant_context: TenantContext, hostname: str
    ) -> CertificateItem:
        certificate = self._visible_certificate(tenant_context, hostname)
        return CertificateItem(**certificate.to_dict())

    def health(self, tenant_context: TenantContext) -> CertificateHealthResponse:
        inventory = self._inventory()
        visible = self._visible(inventory, tenant_context)
        return CertificateHealthResponse(
            **health_view(
                inventory,
                visible_hostnames={cert.hostname for cert in visible},
                is_platform=tenant_context.is_platform,
                config=self._config,
            )
        )

    def alerts(self, tenant_context: TenantContext) -> CertificateAlertsResponse:
        inventory = self._inventory()
        visible = self._visible(inventory, tenant_context)
        payload = load_alerts(self._config)
        view = alert_view(
            payload,
            visible_hostnames={cert.hostname for cert in visible},
            default_name=self._config.default_name,
            is_platform=tenant_context.is_platform,
        )
        return CertificateAlertsResponse(**view)

    def get_job(
        self, tenant_context: TenantContext, hostname: str, job_id: str
    ) -> CertificateJobResponse:
        certificate = self._visible_certificate(tenant_context, hostname)
        try:
            job = self._jobs.job(certificate.hostname, job_id)
        except CertificateValidationError as exc:
            raise AppError(
                str(exc), status_code=422, code="domains.certificate_job_invalid"
            ) from exc
        if job is None:
            raise NotFoundError("Solicitud desconocida")
        return CertificateJobResponse(
            job_id=job.job_id,
            hostname=job.hostname,
            state=job.state,  # type: ignore[arg-type]
            action=job.action,
            requested_at=job.requested_at,
            updated_at=job.updated_at,
            message=job.message,
            error=job.error,
        )

    # ---- escritura -------------------------------------------------------

    def request_certificate(
        self,
        tenant_context: TenantContext,
        hostname: str,
        *,
        action: str = "ensure",
        reason: str | None = None,
    ) -> CertificateRequestResponse:
        certificate = self._visible_certificate(tenant_context, hostname)
        if certificate.scope == "global" or certificate.name == self._config.default_name:
            raise AppError(
                "El certificado global no se regenera desde el panel",
                status_code=409,
                code="domains.certificate_global",
            )
        if action not in ("ensure", "force"):
            raise AppError(
                "Acción inválida", status_code=422, code="domains.certificate_invalid_action"
            )

        try:
            job_id = self._jobs.create_request(
                certificate.hostname,
                action=action,
                requested_by=tenant_context.current_user_id,
                reason=reason,
            )
        except ActiveJobError:
            raise
        except CertificateValidationError as exc:
            raise AppError(
                str(exc), status_code=422, code="domains.certificate_invalid_hostname"
            ) from exc

        self._audit(
            tenant_context,
            action="domains.certificate.requested",
            hostname=certificate.hostname,
            details={"job_id": job_id, "action": action, "reason": reason or "manual"},
        )
        return CertificateRequestResponse(
            job_id=job_id,
            hostname=certificate.hostname,
            state="queued",
            poll_url=(
                "/api/v1/plugins/domains/certificates/"
                f"{certificate.hostname}/jobs/{job_id}"
            ),
        )

    def refresh(self, tenant_context: TenantContext) -> CertificateRefreshResponse:
        request_id = self._jobs.create_refresh(
            requested_by=tenant_context.current_user_id, reason="manual"
        )
        self._audit(
            tenant_context,
            action="domains.certificate.refreshed",
            hostname=None,
            details={"request_id": request_id},
        )
        return CertificateRefreshResponse(requested=True, request_id=request_id, state="queued")

    # ---- acciones administrativas (A.SPEC 0036) --------------------------

    ADMIN_ACTIONS = frozenset({"verify", "sync", "install", "rollback"})

    def _require_platform(self, tenant_context: TenantContext) -> None:
        if not tenant_context.is_platform:
            raise AppError(
                "Solo el administrador de infraestructura puede ejecutar esta acción",
                status_code=403,
                code="domains.certificate_admin_only",
            )

    def admin_action(
        self,
        tenant_context: TenantContext,
        hostname: str,
        *,
        action: str,
        reason: str | None = None,
    ) -> CertificateRequestResponse:
        """Fuerza verificación/instalación/rollback; requiere plataforma."""

        if action not in self.ADMIN_ACTIONS:
            raise AppError(
                "Acción inválida", status_code=422, code="domains.certificate_invalid_action"
            )
        certificate = self._visible_certificate(tenant_context, hostname)
        self._require_platform(tenant_context)
        if (
            action in ("sync", "install", "rollback")
            and certificate.scope == "global"
        ):
            raise AppError(
                "El certificado global no se instala ni revierte desde el panel",
                status_code=409,
                code="domains.certificate_global",
            )
        try:
            job_id = self._jobs.create_request(
                certificate.hostname,
                action=action,
                requested_by=tenant_context.current_user_id,
                reason=reason or "admin",
            )
        except CertificateValidationError as exc:
            raise AppError(
                str(exc), status_code=422, code="domains.certificate_invalid_hostname"
            ) from exc

        audit_action = {
            "verify": "domains.certificate.verified",
            "sync": "domains.certificate.synced",
            "install": "domains.certificate.installed",
            "rollback": "domains.certificate.rolled_back",
        }[action]
        self._audit(
            tenant_context,
            action=audit_action,
            hostname=certificate.hostname,
            details={"job_id": job_id, "action": action, "reason": reason or "admin"},
        )
        return CertificateRequestResponse(
            job_id=job_id,
            hostname=certificate.hostname,
            state="queued",
            poll_url=(
                "/api/v1/plugins/domains/certificates/"
                f"{certificate.hostname}/jobs/{job_id}"
            ),
        )

    # ---- auditoría -------------------------------------------------------

    def _audit(
        self,
        tenant_context: TenantContext,
        *,
        action: str,
        hostname: str | None,
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
            entity_type="certificate",
            entity_id=hostname or "certificates",
            result="success",
            correlation_id=None,
            request_id=None,
            details=details,
        )
        self._db.commit()
