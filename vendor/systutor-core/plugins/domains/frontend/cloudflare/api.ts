import { ApiError, apiRequest } from "@systutor/shell/api/client";

import type {
  CloudflareClientStatus,
  CloudflareDisconnectResponse,
  CloudflareProvisionApply,
  CloudflareProvisionPlan,
  CloudflareRecord,
  CloudflareRecordDeleteResponse,
  CloudflareRecordList,
  CloudflareRecordWrite,
  CloudflareStartResponse,
  CloudflareStatus,
  CloudflareZoneList,
} from "./types";

const BASE = "/api/v1/plugins/domains/dns/cloudflare";

export const cloudflareKeys = {
  all: ["domains", "cloudflare"] as const,
  status: ["domains", "cloudflare", "status"] as const,
  client: ["domains", "cloudflare", "client"] as const,
  zones: ["domains", "cloudflare", "zones"] as const,
  records: (zoneId: string) => ["domains", "cloudflare", "zones", zoneId, "records"] as const,
};

export const getCloudflareClient = () =>
  apiRequest<CloudflareClientStatus>(`${BASE}/oauth/client`);

export const registerCloudflareClient = (payload: {
  bootstrap_token: string;
  account_id?: string;
}) =>
  apiRequest<CloudflareClientStatus>(`${BASE}/oauth/register`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const startCloudflareOAuth = () =>
  apiRequest<CloudflareStartResponse>(`${BASE}/oauth/start`, { method: "POST" });

/** A tenant without a connection is a normal state, not a transport failure. */
export async function getCloudflareStatus(): Promise<CloudflareStatus | null> {
  try {
    return await apiRequest<CloudflareStatus>(`${BASE}/status`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

export const listCloudflareZones = () =>
  apiRequest<CloudflareZoneList>(`${BASE}/zones`);

export const listCloudflareRecords = (zoneId: string) =>
  apiRequest<CloudflareRecordList>(`${BASE}/zones/${encodeURIComponent(zoneId)}/records`);

export const createCloudflareRecord = (zoneId: string, payload: CloudflareRecordWrite) =>
  apiRequest<CloudflareRecord>(`${BASE}/zones/${encodeURIComponent(zoneId)}/records`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateCloudflareRecord = (
  zoneId: string,
  recordId: string,
  payload: CloudflareRecordWrite,
) => apiRequest<CloudflareRecord>(
  `${BASE}/zones/${encodeURIComponent(zoneId)}/records/${encodeURIComponent(recordId)}`,
  { method: "PUT", body: JSON.stringify(payload) },
);

export const deleteCloudflareRecord = (zoneId: string, recordId: string) =>
  apiRequest<CloudflareRecordDeleteResponse>(
    `${BASE}/zones/${encodeURIComponent(zoneId)}/records/${encodeURIComponent(recordId)}`,
    { method: "DELETE" },
  );

export const disconnectCloudflare = () =>
  apiRequest<CloudflareDisconnectResponse>(`${BASE}/disconnect`, {
    method: "POST",
    body: JSON.stringify({ apply: true }),
  });

export const provisionPlan = (payload: {
  domain: string;
  ipv4: string;
  www: boolean;
  replace: boolean;
}) =>
  apiRequest<CloudflareProvisionPlan>(`${BASE}/provision/plan`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const provisionApply = (payload: {
  domain: string;
  ipv4: string;
  www: boolean;
  replace: boolean;
}) =>
  apiRequest<CloudflareProvisionApply>(`${BASE}/provision/apply`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
