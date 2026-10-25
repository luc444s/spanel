import { apiRequest } from "@systutor/shell/api/client";

export type DomainOwner = "hosting" | "domains";

export type DomainItem = {
  domain: string;
  site_id: string | null;
  container: string | null;
  owner: DomainOwner;
  editable: boolean;
  conflict: boolean;
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

export function removeDomain(domain: string) {
  return apiRequest<void>(`${BASE}/${encodeURIComponent(domain)}`, { method: "DELETE" });
}

export function getCertificate(domain: string) {
  return apiRequest<CertificateInfo>(
    `${BASE}/${encodeURIComponent(domain)}/certificate`
  );
}
