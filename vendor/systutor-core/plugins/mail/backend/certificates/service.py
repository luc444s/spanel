"""Casos de uso del gestor de certificados de correo.

La API nunca emite ni instala: filtra el inventario por tenant, escribe
solicitudes validadas y lee el resultado que publica el agente.
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.orm import Session

from plugins.mail.backend.certificates.config import CertificatesConfig
from plugins.mail.backend.certificates.health import (
    alert_view,
    health_view,
    load_alerts,
)
from plugins.mail.backend.certificates.inventory import Inventory, load_inventory
from plugins.mail.backend.certificates.jobs import ActiveJobError, JobStore
from plugins.mail.backend.certificates.models import Certificate
from plugins.mail.backend.certificates.schemas import (
    CertificateAlertsResponse,
    CertificateHealthResponse,
    CertificateItem,
    CertificateJobResponse,
    CertificateRefreshResponse,
    CertificateRequestResponse,
    CertificatesResponse,
)
from plugins.mail.backend.certificates.validation import (
    CertificateValidationError,
    normalize_hostname,
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
        configured = getattr(app.state, "mail_certificates_config", None)
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
        # El correo usa el dominio primario del sitio. Los dominios extra son
        # rutas web del proxy y no se mezclan aquí.
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
                str(exc), status_code=422, code="mail.certificates_invalid_hostname"
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

    def _normalize_or_422(self, hostname: str) -> str:
        try:
            return normalize_hostname(hostname)
        except CertificateValidationError as exc:
            raise AppError(
                str(exc), status_code=422, code="mail.certificates_invalid_hostname"
            ) from exc

    def _is_new_hostname_allowed(
        self, tenant_context: TenantContext, needle: str
    ) -> bool:
        """Alta de dominio nuevo (A.SPEC 0041).

        Un hostname ausente del inventario solo se acepta como el host de
        correo de un dominio: `mail.<dominio>`. Un tenant solo puede pedir
        `mail.<d>` cuando `<d>` es un dominio primario propio; el administrador
        de plataforma (`core.platform.admin`) puede pedir cualquier `mail.*`
        válido. El resto responde 404 para no filtrar la existencia.
        """

        rest = needle[len("mail.") :] if needle.startswith("mail.") else ""
        if not rest:
            return False
        if tenant_context.is_platform:
            return True
        return rest in self._tenant_domains(tenant_context.current_tenant_id)

    def _resolve_request_hostname(
        self, tenant_context: TenantContext, hostname: str
    ) -> tuple[str, Certificate | None]:
        """Resuelve un hostname para request/get_job.

        Devuelve el hostname destino (`certificate.hostname` para certificados
        existentes, el hostname normalizado para altas) o levanta: 422 si el
        forma del hostname es inválida, 404 si el certificado existe pero el
        tenant no lo ve, o 404 si el hostname es una alta no permitida.
        """

        needle = self._normalize_or_422(hostname)
        certificate = self._inventory().find(needle)
        if certificate is None:
            if not self._is_new_hostname_allowed(tenant_context, needle):
                raise NotFoundError("Certificado desconocido")
            return needle, None
        if not certificate.visible_to(
            tenant_id=tenant_context.current_tenant_id,
            is_platform=tenant_context.is_platform,
            tenant_domains=self._tenant_domains(tenant_context.current_tenant_id),
        ):
            # 404 para no filtrar la existencia de hostnames de otros tenants.
            raise NotFoundError("Certificado desconocido")
        return certificate.hostname, certificate

    # ---- lectura ---------------------------------------------------------

    def list_certificates(
        self, tenant_context: TenantContext, *, tenant_only: bool = False
    ) -> CertificatesResponse:
        inventory = self._inventory()
        domains = self._tenant_domains(tenant_context.current_tenant_id)
        is_platform = tenant_context.is_platform and not tenant_only
        items = [
            CertificateItem(**cert.to_dict())
            for cert in inventory.certificates
            if cert.visible_to(
                tenant_id=tenant_context.current_tenant_id,
                is_platform=is_platform,
                tenant_domains=domains,
            )
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
        target, _ = self._resolve_request_hostname(tenant_context, hostname)
        try:
            job = self._jobs.job(target, job_id)
        except CertificateValidationError as exc:
            raise AppError(
                str(exc), status_code=422, code="mail.certificate_job_invalid"
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
        target, certificate = self._resolve_request_hostname(
            tenant_context, hostname
        )
        if certificate is not None and (
            certificate.scope == "global"
            or certificate.name == self._config.default_name
        ):
            raise AppError(
                "El certificado global no se regenera desde el panel",
                status_code=409,
                code="mail.certificate_global",
            )
        if action not in ("ensure", "force"):
            raise AppError(
                "Acción inválida", status_code=422, code="mail.certificate_invalid_action"
            )

        try:
            job_id = self._jobs.create_request(
                target,
                action=action,
                requested_by=tenant_context.current_user_id,
                reason=reason,
            )
        except ActiveJobError:
            raise
        except CertificateValidationError as exc:
            raise AppError(
                str(exc), status_code=422, code="mail.certificate_invalid_hostname"
            ) from exc

        self._audit(
            tenant_context,
            action="mail.certificate.requested",
            hostname=target,
            details={"job_id": job_id, "action": action, "reason": reason or "manual"},
        )
        return CertificateRequestResponse(
            job_id=job_id,
            hostname=target,
            state="queued",
            poll_url=(
                f"/api/v1/plugins/mail/certificates/{target}/jobs/{job_id}"
            ),
        )

    def refresh(self, tenant_context: TenantContext) -> CertificateRefreshResponse:
        request_id = self._jobs.create_refresh(
            requested_by=tenant_context.current_user_id, reason="manual"
        )
        self._audit(
            tenant_context,
            action="mail.certificate.refreshed",
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
                code="mail.certificate_admin_only",
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
                "Acción inválida", status_code=422, code="mail.certificate_invalid_action"
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
                code="mail.certificate_global",
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
                str(exc), status_code=422, code="mail.certificate_invalid_hostname"
            ) from exc

        audit_action = {
            "verify": "mail.certificate.verified",
            "sync": "mail.certificate.synced",
            "install": "mail.certificate.installed",
            "rollback": "mail.certificate.rolled_back",
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
                "/api/v1/plugins/mail/certificates/"
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
