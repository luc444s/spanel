import { apiRequest } from "@systutor/shell/api/client";

import type {
  AppAction, Application, ApplicationCreate, Deployment, EnvironmentEntry,
  GithubConnection, Operation, OperationSummary,
} from "./types";

const ROOT = "/api/v1/plugins/apps";

export const appsKeys = {
  applications: ["apps", "applications"] as const,
  application: (id: string) => ["apps", "applications", id] as const,
  deployments: (id: string) => ["apps", "applications", id, "deployments"] as const,
  connection: ["apps", "github", "connection"] as const,
  operation: (id: string) => ["apps", "operations", id] as const,
};

export function newIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

const operationOptions = (method: string, key: string, body?: unknown) => ({
  method,
  headers: { "Idempotency-Key": key },
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});

export const getGithubConnection = () => apiRequest<GithubConnection>(`${ROOT}/github/connection`);
export const startGithubOAuth = () => apiRequest<{ authorization_url: string }>(`${ROOT}/github/oauth/start`, {
  method: "POST", body: JSON.stringify({ redirect: "/app/apps" }),
});
export const disconnectGithub = () => apiRequest<void>(`${ROOT}/github/disconnect`, { method: "POST" });

export const listApplications = () => apiRequest<Application[]>(`${ROOT}/applications`);
export const getApplication = (id: string) => apiRequest<Application>(`${ROOT}/applications/${encodeURIComponent(id)}`);
export const createApplication = (body: ApplicationCreate) => apiRequest<Application>(`${ROOT}/applications`, {
  method: "POST", body: JSON.stringify(body),
});
export const reconnectSource = (id: string, sourceConnectionUserId: string) => apiRequest<void>(
  `${ROOT}/applications/${encodeURIComponent(id)}/source/reconnect`, {
    method: "POST", body: JSON.stringify({ source_connection_user_id: sourceConnectionUserId }),
  },
);
export const inspectApplication = (id: string, key: string) => apiRequest<OperationSummary>(
  `${ROOT}/applications/${encodeURIComponent(id)}/inspect`, operationOptions("POST", key),
);
export const deployApplication = (id: string, environment: EnvironmentEntry[], key: string) =>
  apiRequest<OperationSummary>(`${ROOT}/applications/${encodeURIComponent(id)}/deployments`,
    operationOptions("POST", key, { environment: environment.map(({ name, value }) => [name, value]) }));
export const runApplicationAction = (id: string, action: AppAction, key: string) =>
  apiRequest<OperationSummary>(`${ROOT}/applications/${encodeURIComponent(id)}/actions/${action}`,
    operationOptions("POST", key));
export const deleteApplication = (id: string, confirm: string, purgeData: boolean, key: string) =>
  apiRequest<OperationSummary>(`${ROOT}/applications/${encodeURIComponent(id)}`,
    operationOptions("DELETE", key, { confirm, purge_data: purgeData }));
export const listDeployments = (id: string) => apiRequest<Deployment[]>(
  `${ROOT}/applications/${encodeURIComponent(id)}/deployments`,
);
export const getOperation = (id: string) => apiRequest<Operation>(`${ROOT}/operations/${encodeURIComponent(id)}`);
