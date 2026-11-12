import { apiRequest, getApiBaseUrl, ApiError } from "@systutor/shell/api/client";
import { useAuthStore } from "../../../../../../apps/web/src/features/auth/store";
import type {
  NameserverPlan, NameserverSettings, NameserverState, Plan, RrsetDraft,
  VerifyResult, Zone, ZoneList,
} from "./types";

const BASE = "/api/v1/plugins/domains/dns";
const zonePath = (zone: string) => `${BASE}/zones/${encodeURIComponent(zone)}`;

export const dnsKeys = {
  all: ["domains", "dns"] as const,
  zones: ["domains", "dns", "zones"] as const,
  zone: (zone: string) => ["domains", "dns", "zones", zone] as const,
  nameservers: ["domains", "dns", "nameservers"] as const,
};

const json = (value: unknown) => JSON.stringify(value);
const post = <T>(path: string, body: unknown) => apiRequest<T>(path, { method: "POST", body: json(body) });

export const listZones = () => apiRequest<ZoneList>(`${BASE}/zones`);
export const getZone = (zone: string) => apiRequest<Zone>(`${zonePath(zone)}/rrsets`);
export const createZone = (zone_name: string) => post<Zone>(`${BASE}/zones`, { zone_name });
export const planRrsets = (zone: string, rrsets: RrsetDraft[]) => post<Plan>(`${zonePath(zone)}/rrsets/plan`, { rrsets });
export const applyRrsets = (zone: string, rrsets: RrsetDraft[], expected_version: string) =>
  post<Zone>(`${zonePath(zone)}/rrsets/apply`, { rrsets, expected_version });
export const deleteRrset = (zone: string, name: string, type: string, expected_version: string) =>
  post<Zone>(`${zonePath(zone)}/rrsets/delete`, { name, type, expected_version });
export const importZone = (zone: string, content: string, expected_version: string) =>
  post<Zone>(`${zonePath(zone)}/import`, { content, expected_version });
export const deleteZone = (zone: string, expectedVersion: string) =>
  apiRequest(`${zonePath(zone)}?expected_version=${encodeURIComponent(expectedVersion)}`, { method: "DELETE" });
export const getNameservers = () => apiRequest<NameserverState>(`${BASE}/nameservers`);
export const planNameservers = (settings: NameserverSettings, zones: string[]) =>
  post<NameserverPlan>(`${BASE}/nameservers/plan`, { settings, zones });
export const applyNameservers = (payload: ReturnType<typeof import("./logic").buildNameserverApply>) =>
  post(`${BASE}/nameservers/apply`, payload);
export const verifyNameservers = (zone: string, resolver: string, port: number) =>
  post<VerifyResult>(`${BASE}/nameservers/verify`, { zone, resolver, port });

function exportUrl(zone: string) {
  const base = getApiBaseUrl();
  const path = `${zonePath(zone)}/export`;
  return base.endsWith("/api") ? `${base}${path.slice(4)}` : `${base}${path}`;
}

export async function fetchZoneExport(zone: string) {
  const token = useAuthStore.getState().token;
  const response = await fetch(exportUrl(zone), {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) throw new ApiError("Zone export failed.", response.status);
  return response.blob();
}

export async function downloadZone(zone: string) {
  const blob = await fetchZoneExport(zone);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${zone}.zone`;
  anchor.click();
  URL.revokeObjectURL(url);
}
