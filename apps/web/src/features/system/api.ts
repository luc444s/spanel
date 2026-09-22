import { apiRequest } from "@systutor/shell/api/client";
import type { PluginManifest } from "@systutor/shell/api/client";
import type { PluginRuntimeRecord } from "@systutor/shell/api/client";

export type HealthResponse = {
  status: string;
  service: string;
  version: string;
  env: string;
};

export type ReadyResponse = HealthResponse & {
  plugins_loaded: number;
  database_configured: boolean;
  database_connected: boolean;
  redis_configured: boolean;
};

export function getSystemHealth() {
  return apiRequest<HealthResponse>("/api/v1/system/health");
}

export function getSystemReady() {
  return apiRequest<ReadyResponse>("/api/v1/system/ready");
}

export function getPlugins() {
  return apiRequest<PluginManifest[]>("/api/v1/system/plugins");
}

export function getPluginRuntime() {
  return apiRequest<PluginRuntimeRecord[]>("/api/v1/system/plugin-runtime");
}
