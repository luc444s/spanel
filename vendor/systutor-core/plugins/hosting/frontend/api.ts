import { apiRequest } from "@systutor/shell/api/client";

export type HostingSiteKind = "wordpress" | "static";

export type HostingSite = {
  id: string;
  container_name: string;
  domain: string | null;
  status: string;
  managed: boolean;
  kind: HostingSiteKind;
  /** Id of the static profile; null for wordpress. */
  profile: string | null;
  tenant_id: string | null;
  tenant_name: string | null;
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

export type BackupExportResponse = {
  url: string;
  expires_in: number;
  filename: string;
};

export type BackupImportResponse = {
  site_id: string;
  container_name: string;
  restored: boolean;
};

/** A profile for the UI. The backend returns env NAMES, never their values. */
export type StaticProfile = {
  id: string;
  name: string;
  image: string;
  web_root: string;
  container_port: number;
  health_mode: "marker" | "http_200";
  env_keys: string[];
  command: string | null;
  builtin: boolean;
};

export type StaticProfileListResponse = { profiles: StaticProfile[] };

export type StaticProfilePayload = {
  id: string;
  name: string;
  image: string;
  web_root: string;
  container_port: number;
  health_mode: "marker" | "http_200";
  env_json: Record<string, string> | null;
  command: string | null;
};

export function listHostingProfiles() {
  return apiRequest<StaticProfileListResponse>(
    "/api/v1/plugins/hosting/static-profiles"
  );
}

export function createHostingProfile(payload: StaticProfilePayload) {
  return apiRequest<StaticProfile>("/api/v1/plugins/hosting/static-profiles", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateHostingProfile(
  profileId: string,
  payload: Partial<Omit<StaticProfilePayload, "id">>
) {
  return apiRequest<StaticProfile>(
    `/api/v1/plugins/hosting/static-profiles/${encodeURIComponent(profileId)}`,
    { method: "PUT", body: JSON.stringify(payload) }
  );
}

export function deleteHostingProfile(profileId: string) {
  return apiRequest<void>(
    `/api/v1/plugins/hosting/static-profiles/${encodeURIComponent(profileId)}`,
    { method: "DELETE" }
  );
}

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
  profiles: ["hosting", "profiles"] as const,
};

export function createHostingSite(payload: {
  kind: HostingSiteKind;
  name: string;
  domain: string;
  profile?: string;
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

export function exportHostingBackup(siteId: string) {
  return apiRequest<BackupExportResponse>(
    `/api/v1/plugins/hosting/sites/${encodeURIComponent(siteId)}/backup/export`,
    { method: "POST" }
  );
}

export function importHostingBackup(siteId: string, file: File, confirmation: string) {
  const body = new FormData();
  body.append("file", file);
  body.append("confirmation", confirmation);
  return apiRequest<BackupImportResponse>(
    `/api/v1/plugins/hosting/sites/${encodeURIComponent(siteId)}/backup/import`,
    { method: "POST", body }
  );
}

export function listHostingSites(platform = false) {
  const scope = platform ? "?scope=all" : "";
  return apiRequest<HostingSiteListResponse>(`/api/v1/plugins/hosting/sites${scope}`);
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
