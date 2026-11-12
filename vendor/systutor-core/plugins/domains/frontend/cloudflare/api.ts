import { ApiError, apiRequest } from "@systutor/shell/api/client";

import type {
  CloudflareClientStatus,
  CloudflareDisconnectResponse,
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

export const disconnectCloudflare = () =>
  apiRequest<CloudflareDisconnectResponse>(`${BASE}/disconnect`, {
    method: "POST",
    body: JSON.stringify({ apply: true }),
  });
