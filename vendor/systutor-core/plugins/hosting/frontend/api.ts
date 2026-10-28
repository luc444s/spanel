import { apiRequest } from "@systutor/shell/api/client";

export type HostingSiteKind = "wordpress" | "static";

export type HostingSite = {
  id: string;
  container_name: string;
  domain: string | null;
  status: string;
  managed: boolean;
  kind: HostingSiteKind;
};

export type DomainCheck = {
  available: boolean;
  reason: string | null;
  suffix: string | null;
};

export type HostingSiteListResponse = {
  sites: HostingSite[];
};

export type WordPressUser = {
  id: number;
  login: string;
  email: string;
  status: number;
  role: string;
};

export type WordPressUserListResponse = {
  site_id: string;
  container_name: string;
  users: WordPressUser[];
};

export type ResetPasswordResponse = {
  site_id: string;
  user_id: number;
  login: string;
  generated: boolean;
  password: string | null;
};

export type HostingSiteConfig = {
  site_id: string;
  container_name: string;
  /** Values of the whitelisted keys. Never contains a secret. */
  editable_env: Record<string, string>;
  /** Names of the secrets present. Their values are never returned. */
  secret_names: string[];
  /** The whitelist the server accepts, so the UI does not hardcode it. */
  editable_keys: string[];
};

export type SecretRotationResponse = {
  site_id: string;
  container_name: string;
  rotated: string[];
};

export type HostingSiteDatabase = {
  site_id: string;
  container_name: string;
  /** Docker container of the database, derived from the owned site. */
  host: string;
  database: string;
  user: string;
  password: string;
  /** phpMyAdmin login page, or null when the panel has no URL configured. */
  url: string | null;
};

export type PhpMyAdminSessionResponse = { url: string; expires_in: number };

export function createPhpMyAdminSession(siteId: string) {
  return apiRequest<PhpMyAdminSessionResponse>(
    `/api/v1/plugins/hosting/sites/${encodeURIComponent(siteId)}/phpmyadmin/session`,
    { method: "POST" }
  );
}

export function closePhpMyAdminSession(siteId: string) {
  return apiRequest<void>(
    `/api/v1/plugins/hosting/sites/${encodeURIComponent(siteId)}/phpmyadmin/session`,
    { method: "DELETE" }
  );
}

export const hostingKeys = {
  sites: ["hosting", "sites"] as const,
  users: (siteId: string) => ["hosting", siteId, "users"] as const,
  config: (siteId: string) => ["hosting", siteId, "config"] as const,
};

export function createHostingSite(payload: {
  kind: HostingSiteKind;
  name: string;
  domain: string;
}) {
  return apiRequest<{ site: HostingSite; admin_password: string | null }>(
    "/api/v1/plugins/hosting/sites",
    {
      method: "POST", body: JSON.stringify(payload),
    }
  );
}

/**
 * Ask the domains plugin whether this domain belongs to the tenant and is free.
 * The backend owns the suffix; the UI never guesses it.
 */
export function checkHostingDomain(domain: string) {
  return apiRequest<DomainCheck>(
    `/api/v1/plugins/domains/check?domain=${encodeURIComponent(domain)}`
  );
}

export function operateHostingSite(siteId: string, action: "start" | "stop" | "restart") {
  return apiRequest<HostingSite>(
    `/api/v1/plugins/hosting/sites/${encodeURIComponent(siteId)}/${action}`,
    { method: "POST" }
  );
}

export function hostingSiteLogs(siteId: string, lines = 200) {
  return apiRequest<{ logs: string }>(
    `/api/v1/plugins/hosting/sites/${encodeURIComponent(siteId)}/logs?lines=${lines}`
  );
}

export function deleteHostingSite(siteId: string) {
  return apiRequest<void>(`/api/v1/plugins/hosting/sites/${encodeURIComponent(siteId)}`, {
    method: "DELETE",
  });
}

export function listHostingSites() {
  return apiRequest<HostingSiteListResponse>("/api/v1/plugins/hosting/sites");
}

export function listSiteUsers(siteId: string) {
  return apiRequest<WordPressUserListResponse>(
    `/api/v1/plugins/hosting/sites/${encodeURIComponent(siteId)}/users`
  );
}

export function getHostingSiteConfig(siteId: string) {
  return apiRequest<HostingSiteConfig>(
    `/api/v1/plugins/hosting/sites/${encodeURIComponent(siteId)}/config`
  );
}

export function updateHostingSiteConfig(siteId: string, env: Record<string, string>) {
  return apiRequest<HostingSiteConfig>(
    `/api/v1/plugins/hosting/sites/${encodeURIComponent(siteId)}/config`,
    { method: "PATCH", body: JSON.stringify({ env }) }
  );
}

export function rotateHostingSiteSalts(siteId: string) {
  return apiRequest<SecretRotationResponse>(
    `/api/v1/plugins/hosting/sites/${encodeURIComponent(siteId)}/secrets/salts`,
    { method: "POST" }
  );
}

/**
 * Reveal the site's own database credential. It is a POST on purpose: the
 * password must not live in a cache, and the URL must not carry it.
 */
export function openHostingSiteDatabase(siteId: string) {
  return apiRequest<HostingSiteDatabase>(
    `/api/v1/plugins/hosting/sites/${encodeURIComponent(siteId)}/database`,
    { method: "POST" }
  );
}

export function resetUserPassword(siteId: string, userId: number) {
  return apiRequest<ResetPasswordResponse>(
    `/api/v1/plugins/hosting/sites/${encodeURIComponent(siteId)}/users/${userId}/password`,
    {
      method: "POST",
      // The body omits `password` on purpose: the server generates one and
      // returns it exactly once.
      body: JSON.stringify({}),
    }
  );
}

// ---- Certificados TLS de correo (A.SPEC 0035) ---------------------------

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
