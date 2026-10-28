import { apiRequest } from "@systutor/shell/api/client";

export type DomainOwner = "hosting" | "domains";

export type DomainItem = {
  domain: string;
  site_id: string | null;
  container: string | null;
  owner: DomainOwner;
  editable: boolean;
  conflict: boolean;
  outside_tenant: boolean;
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
  site_id: string;
  domain: string;
  www: boolean;
};

export const domainsKeys = {
  list: ["domains", "list"] as const,
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

export function removeDomain(domain: string) {
  return apiRequest<void>(`${BASE}/${encodeURIComponent(domain)}`, { method: "DELETE" });
}

export function getCertificate(domain: string) {
  return apiRequest<CertificateInfo>(
    `${BASE}/${encodeURIComponent(domain)}/certificate`
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

// ---- Inventario del gestor TLS de correo (A.SPEC 0035) ------------------

export type MailCertificate = {
  hostname: string;
  name: string;
  scope: "global" | "tenant";
  domain: string | null;
  source: "certbot" | "static" | "step-ca";
  deployment: "default" | "sni" | "none" | "failed";
  status: "active" | "expiring" | "expired" | "missing" | "error";
  sans: string[];
  issuer: string | null;
  subject: string | null;
  serial: string | null;
  fingerprint: string | null;
  not_before: string | null;
  not_after: string | null;
  cert_path: string | null;
  key_path: string | null;
  last_checked_at: string | null;
  last_issued_at: string | null;
  last_error: string | null;
};

export type MailCertificatesResponse = {
  items: MailCertificate[];
  stale: boolean;
  generated_at: string | null;
  agent_last_seen: string | null;
  error: string | null;
};

export type MailCertificateJob = {
  job_id: string;
  hostname: string;
  state: "queued" | "running" | "succeeded" | "failed";
  action: string | null;
  requested_at: string | null;
  updated_at: string | null;
  message: string | null;
  error: string | null;
};

export type MailCertificateRequestResponse = {
  job_id: string;
  hostname: string;
  state: "queued";
  poll_url: string;
};

export const certificateKeys = {
  list: ["domains", "certificates"] as const,
  job: (hostname: string, jobId: string) =>
    ["domains", "certificates", hostname, "jobs", jobId] as const,
};

const CERTIFICATES_BASE = "/api/v1/plugins/domains/certificates";

export function listMailCertificates() {
  return apiRequest<MailCertificatesResponse>(CERTIFICATES_BASE);
}

export function requestMailCertificate(
  hostname: string,
  action: "ensure" | "force",
  reason = "manual"
) {
  return apiRequest<MailCertificateRequestResponse>(
    `${CERTIFICATES_BASE}/${encodeURIComponent(hostname)}/request`,
    { method: "POST", body: JSON.stringify({ action, reason }) }
  );
}

export function getMailCertificateJob(hostname: string, jobId: string) {
  return apiRequest<MailCertificateJob>(
    `${CERTIFICATES_BASE}/${encodeURIComponent(hostname)}/jobs/${encodeURIComponent(jobId)}`
  );
}

export function refreshMailCertificates() {
  return apiRequest<{ requested: boolean; request_id: string | null; state: "queued" }>(
    `${CERTIFICATES_BASE}/refresh`,
    { method: "POST", body: JSON.stringify({}) }
  );
}

export const certificateHealthKeys = {
  health: ["domains", "certificates", "health"] as const,
  alerts: ["domains", "certificates", "alerts"] as const,
};

export function getMailCertificateHealth() {
  return apiRequest<CertificateHealth>(`${CERTIFICATES_BASE}/health`);
}

export function getMailCertificateAlerts() {
  return apiRequest<CertificateAlerts>(`${CERTIFICATES_BASE}/alerts`);
}

export function runMailCertificateAction(
  hostname: string,
  action: CertificateAdminAction
) {
  return apiRequest<{ job_id: string; hostname: string; state: "queued"; poll_url: string }>(
    `${CERTIFICATES_BASE}/${encodeURIComponent(hostname)}/${action}`,
    { method: "POST", body: JSON.stringify({ reason: "admin" }) }
  );
}

