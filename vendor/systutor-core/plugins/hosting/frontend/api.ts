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

export const hostingKeys = {
  sites: ["hosting", "sites"] as const,
  users: (siteId: string) => ["hosting", siteId, "users"] as const,
};

export function listHostingSites() {
  return apiRequest<HostingSiteListResponse>("/api/v1/plugins/hosting/sites");
}

export function listSiteUsers(siteId: string) {
  return apiRequest<WordPressUserListResponse>(
    `/api/v1/plugins/hosting/sites/${encodeURIComponent(siteId)}/users`
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
