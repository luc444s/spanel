import { apiRequest } from "@systutor/shell/api/client";

export type HostingSite = {
  id: string;
  container_name: string;
  domain: string | null;
  status: string;
  managed: boolean;
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

export const hostingKeys = {
  sites: ["hosting", "sites"] as const,
  users: (siteId: string) => ["hosting", siteId, "users"] as const,
  config: (siteId: string) => ["hosting", siteId, "config"] as const,
};

export function createHostingSite(payload: { name: string; domain: string; admin_email: string }) {
  return apiRequest<{ site: HostingSite; admin_password: string }>("/api/v1/plugins/hosting/sites", {
    method: "POST", body: JSON.stringify(payload),
  });
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
