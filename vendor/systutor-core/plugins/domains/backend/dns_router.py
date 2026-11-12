"""Thin HTTP surface for tenant-scoped PowerDNS operations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from plugins.domains.backend.dns.client import PowerDnsClient
from plugins.domains.backend.dns.config import PowerDnsConfig
from plugins.domains.backend.dns.errors import DnsConfigError, DnsError
from plugins.domains.backend.dns.nameservers import (
    PublicNameserverSettings,
    build_settings,
)
from plugins.domains.backend.dns.service import DnsService
from plugins.domains.backend.dns.store import get_public_settings
from plugins.domains.backend.dns_schemas import (
    DeleteResponse,
    DoctorResponse,
    NameserverApplyRequest,
    NameserverApplyResponse,
    NameserverPlanRequest,
    NameserverPlanResponse,
    NameserverResponse,
    NameserverSettingsInput,
    NameserverVerifyRequest,
    NameserverVerifyResponse,
    PlanResponse,
    ResolveResponse,
    RrsetApplyRequest,
    RrsetDeleteRequest,
    RrsetPlanRequest,
    ZoneCreateRequest,
    ZoneImportRequest,
    ZoneItem,
    ZoneListResponse,
    ZoneResponse,
)
from systutor.api.deps import get_db_session
from systutor.core.errors import AppError
from systutor.kernel.auth.dependencies import get_current_tenant_context, require_permission
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext

router = APIRouter(tags=["domains-dns"])

READ = "domains.dns.read"
RECORDS = "domains.dns.records"
ZONES = "domains.dns.zones"
NAMESERVERS = "domains.dns.nameservers"


def _client(request: Request) -> PowerDnsClient:
    existing = getattr(request.app.state, "powerdns_client", None)
    if existing is not None:
        return existing
    try:
        client = PowerDnsClient(PowerDnsConfig.from_env())
    except DnsConfigError as exc:
        raise AppError(str(exc), status_code=503, code=exc.code) from exc
    request.app.state.powerdns_client = client
    return client


def _get_service(
    request: Request, db: Session = Depends(get_db_session)
) -> DnsService:
    return DnsService(db, request.app, _client(request))


def _desired(body: NameserverSettingsInput) -> PublicNameserverSettings:
    return build_settings(
        ns1_hostname=body.ns1,
        ns2_hostname=body.ns2,
        ns1_ipv4=body.ns1_ipv4,
        ns1_ipv6=body.ns1_ipv6,
        ns2_ipv4=body.ns2_ipv4,
        ns2_ipv6=body.ns2_ipv6,
        soa_contact=body.soa_contact,
        default_ttl=body.default_ttl,
    )


def _zone_response(payload: dict) -> ZoneResponse:
    return ZoneResponse.model_validate(payload)


@router.get("/dns/doctor", response_model=DoctorResponse)
def doctor(
    request: Request,
    _current_user: User = Depends(require_permission(READ)),
) -> DoctorResponse:
    try:
        config = PowerDnsConfig.from_env()
        checks = config.inspect_key_file()
        client = _client(request)
        client.health()
    except DnsError as exc:
        raise AppError(str(exc), status_code=exc.status_code, code=exc.code) from exc
    return DoctorResponse(
        ok=all(checks.values()),
        authenticated=checks["private"],
        checks={**checks, "authenticated": True},
    )


@router.get("/dns/nameservers", response_model=NameserverResponse)
def show_nameservers(
    _current_user: User = Depends(require_permission(READ)),
    service: DnsService = Depends(_get_service),
) -> NameserverResponse:
    return NameserverResponse.model_validate(service.nameserver_settings())


@router.post("/dns/nameservers/plan", response_model=NameserverPlanResponse)
def plan_nameservers(
    body: NameserverPlanRequest,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(READ)),
    service: DnsService = Depends(_get_service),
    db: Session = Depends(get_db_session),
) -> NameserverPlanResponse:
    desired = _desired(body.settings) if body.settings is not None else get_public_settings(db)
    if desired is None:
        raise AppError(
            "public nameserver settings are not configured",
            status_code=422,
            code="domains.dns.validation",
        )
    return NameserverPlanResponse.model_validate(
        service.plan_nameservers(
            desired,
            zones=body.zones,
            tenant_id=tenant_context.current_tenant_id,
        )
    )


@router.post("/dns/nameservers/apply", response_model=NameserverApplyResponse)
def apply_nameservers(
    body: NameserverApplyRequest,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(
        require_permission(NAMESERVERS, platform_only=True)
    ),
    service: DnsService = Depends(_get_service),
) -> NameserverApplyResponse:
    return NameserverApplyResponse.model_validate(
        service.apply_nameservers(
            _desired(body.settings),
            zones=body.zones,
            expected_versions=body.zone_versions,
            expected_version=body.expected_version,
            context=tenant_context,
        )
    )


@router.post("/dns/nameservers/verify", response_model=NameserverVerifyResponse)
def verify_nameservers(
    body: NameserverVerifyRequest,
    _current_user: User = Depends(require_permission(READ)),
    service: DnsService = Depends(_get_service),
) -> NameserverVerifyResponse:
    return NameserverVerifyResponse.model_validate(
        service.verify_nameservers(
            body.zone, resolver=body.resolver, port=body.port, timeout=body.timeout
        )
    )


@router.get("/dns/zones", response_model=ZoneListResponse)
def list_zones(
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(READ)),
    service: DnsService = Depends(_get_service),
) -> ZoneListResponse:
    items = [ZoneItem.model_validate(item) for item in service.list_zones(tenant_context)]
    return ZoneListResponse(items=items)


@router.post("/dns/zones", response_model=ZoneResponse, status_code=201)
def create_zone(
    body: ZoneCreateRequest,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(ZONES)),
    service: DnsService = Depends(_get_service),
) -> ZoneResponse:
    return _zone_response(service.create_zone(body.zone_name, tenant_context))


@router.get("/dns/resolve", response_model=ResolveResponse)
def resolve_domain(
    domain: str = Query(min_length=1, max_length=253),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(READ)),
    service: DnsService = Depends(_get_service),
) -> ResolveResponse:
    return ResolveResponse.model_validate(service.resolve(domain, tenant_context))


@router.get("/dns/zones/{zone_name}", response_model=ZoneResponse)
def get_zone(
    zone_name: str,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(READ)),
    service: DnsService = Depends(_get_service),
) -> ZoneResponse:
    return _zone_response(service.get_zone(zone_name, tenant_context))


@router.delete("/dns/zones/{zone_name}", response_model=DeleteResponse)
def delete_zone(
    zone_name: str,
    expected_version: str = Query(min_length=1),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(ZONES)),
    service: DnsService = Depends(_get_service),
) -> DeleteResponse:
    return DeleteResponse.model_validate(
        service.delete_zone(zone_name, tenant_context, expected_version=expected_version)
    )


@router.get("/dns/zones/{zone_name}/rrsets", response_model=ZoneResponse)
def list_rrsets(
    zone_name: str,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(READ)),
    service: DnsService = Depends(_get_service),
) -> ZoneResponse:
    return _zone_response(service.list_rrsets(zone_name, tenant_context))


@router.post("/dns/zones/{zone_name}/rrsets/plan", response_model=PlanResponse)
def plan_rrsets(
    zone_name: str,
    body: RrsetPlanRequest,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(RECORDS)),
    service: DnsService = Depends(_get_service),
) -> PlanResponse:
    rrsets = [item.model_dump() for item in body.rrsets]
    return PlanResponse.model_validate(
        service.plan_rrsets(zone_name, rrsets, tenant_context)
    )


@router.post("/dns/zones/{zone_name}/rrsets/apply", response_model=ZoneResponse)
def apply_rrsets(
    zone_name: str,
    body: RrsetApplyRequest,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(RECORDS)),
    service: DnsService = Depends(_get_service),
) -> ZoneResponse:
    rrsets = [item.model_dump() for item in body.rrsets]
    return _zone_response(
        service.apply_rrsets(
            zone_name, rrsets, body.expected_version, tenant_context
        )
    )


@router.post("/dns/zones/{zone_name}/rrsets/delete", response_model=ZoneResponse)
def delete_rrset(
    zone_name: str,
    body: RrsetDeleteRequest,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(RECORDS)),
    service: DnsService = Depends(_get_service),
) -> ZoneResponse:
    return _zone_response(
        service.delete_rrset(
            zone_name,
            name=body.name,
            type=body.type,
            expected_version=body.expected_version,
            context=tenant_context,
        )
    )


@router.post("/dns/zones/{zone_name}/import", response_model=ZoneResponse)
def import_zone(
    zone_name: str,
    body: ZoneImportRequest,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(RECORDS)),
    service: DnsService = Depends(_get_service),
) -> ZoneResponse:
    return _zone_response(
        service.import_zone(
            zone_name,
            body.content,
            tenant_context,
            expected_version=body.expected_version,
        )
    )


@router.get("/dns/zones/{zone_name}/export", response_class=PlainTextResponse)
def export_zone(
    zone_name: str,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(READ)),
    service: DnsService = Depends(_get_service),
) -> PlainTextResponse:
    return PlainTextResponse(service.export_zone(zone_name, tenant_context))
