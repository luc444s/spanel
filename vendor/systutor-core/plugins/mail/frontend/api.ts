import { apiRequest } from "@systutor/shell/api/client";

export type MailAccountsResponse = {
  domain: string;
  domains: string[];
  accounts: string[];
};

export type MailMessageResponse = {
  message: string;
  email: string | null;
};

export type CreateAccountPayload = {
  username: string;
  password: string;
  domain?: string;
};

export type ChangePasswordPayload = {
  password: string;
};

export const mailKeys = {
  accounts: ["mail", "accounts"] as const,
};

export function listMailAccounts() {
  return apiRequest<MailAccountsResponse>("/api/v1/plugins/mail/mail/accounts");
}

export function createMailAccount(payload: CreateAccountPayload) {
  return apiRequest<MailMessageResponse>("/api/v1/plugins/mail/mail/accounts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function changeMailAccountPassword(email: string, payload: ChangePasswordPayload) {
  return apiRequest<MailMessageResponse>(
    `/api/v1/plugins/mail/mail/accounts/${encodeURIComponent(email)}/password`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    }
  );
}

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

const CERTIFICATES_BASE = "/api/v1/plugins/mail/certificates";

export function listMailCertificates(tenantOnly = true) {
  const query = tenantOnly ? "?tenant_only=true" : "";
  return apiRequest<MailCertificatesResponse>(`${CERTIFICATES_BASE}${query}`);
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

