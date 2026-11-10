import { apiRequest } from "@systutor/shell/api/client";

export type DomainOwner = "hosting" | "domains";

export type DomainItem = {
  domain: string;
  site_id: string | null;
  container: string | null;
  owner: DomainOwner;
  editable: boolean;
  conflict: boolean;
  published?: boolean;
};

export type DomainCheckResponse = {
  available: boolean;
  reason: string | null;
  suffix: string | null;
};

export type SiteOption = {
  site_id: string;
  container: string;
  primary_domain: string | null;
};

export type DomainsResponse = {
  items: DomainItem[];
  sites: SiteOption[];
  proxy_available?: boolean;
  proxy_error?: string | null;
};

export type CertificateInfo = {
  domain: string;
  state: string;
  issuer: string | null;
  subject: string | null;
  not_after: string | null;
  sans: string[];
  verified: boolean;
  error: string | null;
};

export type CreateDomainPayload = {
  site_id?: string | null;
  domain: string;
  www: boolean;
};

export type LinkDomainPayload = {
  site_id: string;
  www: boolean;
};

// ---- Rutas gestionadas (A.SPEC 0060) -------------------------------------

export type DomainRoute = {
  id: string;
  domain: string;
  path: string;
  internal_path: string;
  strip_path: boolean;
  port: number;
  entrypoints: string[];
  https: boolean;
  middlewares: string[];
  site_id: string;
  available: boolean;
};

export type DomainRoutePayload = {
  domain: string;
  path?: string;
  internal_path?: string;
  strip_path?: boolean;
  port: number;
  entrypoints?: string[];
  https?: boolean;
  middlewares?: string[];
  site_id: string;
};

export type DomainRouteUpdate = Partial<Omit<DomainRoutePayload, "site_id">> & {
  site_id?: string;
};

export type DomainRouteProbe = {
  route_id: string;
  domain: string;
  path: string;
  http_status: number | null;
  certificate: CertificateInfo | null;
};

export const domainsKeys = {
  list: ["domains", "list"] as const,
  available: ["domains", "available"] as const,
  routes: (siteId?: string) =>
    siteId ? (["domains", "routes", siteId] as const) : (["domains", "routes"] as const),
  route: (id: string) => ["domains", "routes", id] as const,
  certificate: (domain: string) => ["domains", "certificate", domain] as const,
};

const BASE = "/api/v1/plugins/domains";

export function listDomains() {
  return apiRequest<DomainsResponse>(BASE);
}

export function createDomain(payload: CreateDomainPayload) {
  return apiRequest<{ items: DomainItem[] }>(BASE, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function checkDomain(domain: string) {
  return apiRequest<DomainCheckResponse>(
    `${BASE}/check?domain=${encodeURIComponent(domain)}`
  );
}

export function linkDomain(domain: string, payload: LinkDomainPayload) {
  return apiRequest<{ items: DomainItem[] }>(
    `${BASE}/${encodeURIComponent(domain)}/link`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}

export function removeDomain(domain: string) {
  return apiRequest<void>(`${BASE}/${encodeURIComponent(domain)}`, { method: "DELETE" });
}

export function getCertificate(domain: string) {
  return apiRequest<CertificateInfo>(
    `${BASE}/${encodeURIComponent(domain)}/certificate`
  );
}

export function listAvailableDomains() {
  return apiRequest<{
    items: DomainItem[];
    entrypoints: string[];
    middlewares: string[];
  }>(`${BASE}/available`);
}

export function listRoutes(siteId?: string) {
  const query = siteId ? `?site_id=${encodeURIComponent(siteId)}` : "";
  return apiRequest<{ items: DomainRoute[] }>(`${BASE}/routes${query}`);
}

export function getRoute(routeId: string) {
  return apiRequest<DomainRoute>(`${BASE}/routes/${encodeURIComponent(routeId)}`);
}

export function createRoute(payload: DomainRoutePayload) {
  return apiRequest<DomainRoute>(`${BASE}/routes`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateRoute(routeId: string, payload: DomainRouteUpdate) {
  return apiRequest<DomainRoute>(`${BASE}/routes/${encodeURIComponent(routeId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function removeRoute(routeId: string) {
  return apiRequest<void>(`${BASE}/routes/${encodeURIComponent(routeId)}`, {
    method: "DELETE",
  });
}

export function probeRoute(routeId: string) {
  return apiRequest<DomainRouteProbe>(
    `${BASE}/routes/${encodeURIComponent(routeId)}/probe`,
    { method: "POST" }
  );
}

// ---- Salud y alertas del gestor TLS de correo (A.SPEC 0036) -------------

export type CertificateHealthItem = {
  hostname: string;
  serial: string | null;
  served_serial: string | null;
  no_sni_serial: string | null;
  not_after: string | null;
  days_left: number | null;
  sni: Record<string, boolean>;
  no_sni: boolean | null;
  no_sni_ports: Record<string, boolean>;
  ok: boolean | null;
  last_verified_at: string | null;
  last_ok_at: string | null;
  consecutive_failures: number;
  last_error: string | null;
};

export type CertificateDefaultHealth = {
  name: string;
  serial: string | null;
  no_sni_serial: string | null;
  ok: boolean | null;
};

export type CertificateHealth = {
  generated_at: string | null;
  agent_state: string;
  agent_last_run_at: string | null;
  interval_seconds: number;
  stale: boolean;
  default: CertificateDefaultHealth | null;
  certificates: CertificateHealthItem[];
  critical_count: number;
  warning_count: number;
};

export type CertificateAlert = {
  code: string;
  severity: "warning" | "critical";
  hostname: string | null;
  message: string;
  created_at: string | null;
};

export type CertificateAlerts = {
  generated_at: string | null;
  alerts: CertificateAlert[];
  delivery_errors: string[];
};

export type CertificateAdminAction = "verify" | "sync" | "install" | "rollback";
