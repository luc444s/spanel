import type { CloudflareStatus } from "./types";

export const CLOUDFLARE_PERMISSION = "domains.dns.cloudflare";
export const CLOUDFLARE_POLL_INTERVAL_MS = 2000;
export const CLOUDFLARE_FLOW_MAX_MS = 5 * 60 * 1000;

export function canUseCloudflare(permissions: readonly string[]): boolean {
  return permissions.includes(CLOUDFLARE_PERMISSION);
}

export function isPlatformAdmin(permissions: readonly string[]): boolean {
  return permissions.includes("core.platform.admin");
}

export function needsClientRegistration(
  status: { registered: boolean } | null | undefined,
): boolean {
  return status?.registered === false;
}

export function canRegisterClient(
  permissions: readonly string[],
  status: { registered: boolean } | null | undefined,
): boolean {
  return isPlatformAdmin(permissions) && needsClientRegistration(status);
}

export function isCloudflareConnected(status: CloudflareStatus | null): boolean {
  return Boolean(status?.connected && status.status === "active");
}

/** Deadline derived from the backend flow expiry, clamped to five minutes. */
export function flowDeadline(expiresAt: string | null | undefined, now: number): number {
  const parsed = expiresAt ? Date.parse(expiresAt) : Number.NaN;
  const limit = now + CLOUDFLARE_FLOW_MAX_MS;
  if (!Number.isFinite(parsed)) {
    return limit;
  }
  return Math.min(parsed, limit);
}

export type PollDecision = "connected" | "expired" | "continue";

export function pollDecision(
  status: CloudflareStatus | null,
  deadline: number,
  now: number,
): PollDecision {
  if (isCloudflareConnected(status)) {
    return "connected";
  }
  return now >= deadline ? "expired" : "continue";
}

export function accountLabel(status: CloudflareStatus | null): string {
  return status?.account_name?.trim() || "Cloudflare";
}

export function scopeNames(status: CloudflareStatus | null): string {
  return (status?.scopes ?? []).join(", ");
}

export function zoneCount(items: readonly unknown[] | null | undefined): number {
  return items?.length ?? 0;
}

/**
 * Normalized user message for a failed Cloudflare request. Never includes an
 * upstream response body, a token or an account identifier.
 */
export function cloudflareErrorMessage(
  httpStatus: number | null,
  code: string | null = null,
): string {
  if (code === "domains.dns.cloudflare.account_id_required") {
    return "Enter the Cloudflare Account ID shown in the account dashboard.";
  }
  switch (httpStatus) {
    case 401:
      return "Your session expired. Sign in again.";
    case 403:
      return "You do not have permission to manage the Cloudflare connection.";
    case 404:
      return "Cloudflare is not connected for this tenant.";
    case 409:
      return "Cloudflare already has a pending authorization or a registered client.";
    case 422:
      return "Cloudflare is not configured correctly on this server. The OAuth client may not be registered yet.";
    case 429:
      return "Cloudflare is rate limiting requests. Try again in a moment.";
    case 502:
      return "Cloudflare returned an invalid response.";
    case 503:
      return "Cloudflare is unavailable right now.";
    case 504:
      return "Cloudflare did not respond in time.";
    default:
      if (code === "domains.dns.cloudflare.conflict") {
        return "Cloudflare already has a pending authorization or a registered client.";
      }
      return "Could not reach Cloudflare. Try again.";
  }
}

/** Convert an unknown thrown value into a normalized message without leaking it. */
export function errorMessageFrom(error: unknown): string {
  if (typeof error === "object" && error !== null) {
    const candidate = error as { status?: unknown; code?: unknown };
    const status = typeof candidate.status === "number" ? candidate.status : null;
    const code = typeof candidate.code === "string" ? candidate.code : null;
    return cloudflareErrorMessage(status, code);
  }
  return cloudflareErrorMessage(null);
}
