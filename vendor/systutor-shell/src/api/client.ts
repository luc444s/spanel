const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);
export const AUTH_UNAUTHORIZED_EVENT = "systutor:auth-unauthorized";

export type PluginManifest = {
  id: string;
  name: string;
  version: string;
  api_version: string;
  requires: string[];
  backend_entrypoint: string;
  frontend_entrypoint: string;
  permissions: string[];
  events: string[];
  description: string;
};

export type PluginRuntimeRecord = {
  id: string;
  plugin_id: string;
  name: string;
  version: string;
  api_version: string;
  state: string;
  is_enabled: boolean;
  backend_entrypoint: string | null;
  frontend_entrypoint: string | null;
  requires_json: string[];
  permissions_json: string[];
  events_json: string[];
  description: string | null;
  migration_version: string | null;
  installed_at: string | null;
  enabled_at: string | null;
  disabled_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function isLoopbackHost(hostname: string) {
  return LOOPBACK_HOSTS.has(hostname);
}

export function getApiBaseUrl() {
  const configuredBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE_URL).replace(/\/$/, "");

  if (typeof window === "undefined") {
    return configuredBaseUrl;
  }

  try {
    const configuredUrl = new URL(configuredBaseUrl);
    if (isLoopbackHost(configuredUrl.hostname) && !isLoopbackHost(window.location.hostname)) {
      return window.location.origin;
    }
  } catch {
    // Keep the configured base when it is already relative or otherwise non-URL.
  }

  return configuredBaseUrl;
}

function buildUrl(path: string) {
  const baseUrl = getApiBaseUrl();
  if (baseUrl.endsWith("/api") && path.startsWith("/api/")) {
    return `${baseUrl}${path.slice(4)}`;
  }
  return `${baseUrl}${path}`;
}

export type TokenProvider = () => string | null;

let tokenProvider: TokenProvider = () => null;

export function setTokenProvider(provider: TokenProvider) {
  tokenProvider = provider;
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const token = tokenProvider();
  const headers = new Headers(init?.headers);
  const isFormData = typeof FormData !== "undefined" && init?.body instanceof FormData;

  if (!headers.has("Content-Type") && init?.body && !isFormData) {
    headers.set("Content-Type", "application/json");
  }

  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(buildUrl(path), {
    ...init,
    headers,
  });

  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  let payload: Record<string, unknown> | null = null;
  if (isJson && response.status !== 204) {
    const text = await response.text();
    payload = text ? JSON.parse(text) : null;
  }

  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(AUTH_UNAUTHORIZED_EVENT));
    }
    const detail = payload?.detail;
    let message: string;
    if (Array.isArray(detail)) {
      message = (detail as Array<{ msg?: string; loc?: unknown[] }>)
        .map((error) => error.msg)
        .filter(Boolean)
        .join(". ");
      if (!message) {
        message = `HTTP ${response.status} al consultar la API`;
      }
    } else if (typeof detail === "object" && detail !== null && "message" in detail) {
      message = String((detail as Record<string, unknown>).message);
    } else if (typeof detail === "string") {
      message = detail;
    } else {
      message = (payload?.message as string) ?? `HTTP ${response.status} al consultar la API`;
    }
    throw new ApiError(message, response.status);
  }

  return payload as T;
}
