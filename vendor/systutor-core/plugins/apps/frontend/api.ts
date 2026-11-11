import { apiRequest } from "@systutor/shell/api/client";

import type {
  AppAction, Application, ApplicationCreate, ApplicationDetail, DeploymentDetail,
  EnvironmentEntry, GithubInstallation, GithubManifestStart, GithubProvider,
  GithubRepository, GithubStatus, Operation, OperationSummary,
} from "./types";

const ROOT = "/api/v1/plugins/apps";

export const appsKeys = {
  applications: ["apps", "applications"] as const,
  application: (id: string) => ["apps", "applications", id] as const,
  detail: (id: string) => ["apps", "applications", id, "detail"] as const,
  operations: (id: string) => ["apps", "applications", id, "operations"] as const,
  deployments: (id: string) => ["apps", "applications", id, "deployments"] as const,
  status: ["apps", "github", "status"] as const,
  providers: ["apps", "github", "providers"] as const,
  installations: ["apps", "github", "installations"] as const,
  repositories: (id: string) => ["apps", "github", "installations", id, "repositories"] as const,
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

export const getGithubStatus = () => apiRequest<GithubStatus>(`${ROOT}/github/status`);
export const startGithubManifest = (ownerType: "personal" | "organization", organization: string | null) =>
  apiRequest<GithubManifestStart>(`${ROOT}/github/manifest/start`, {
    method: "POST",
    body: JSON.stringify({ owner_type: ownerType, organization }),
  });
export const listProviders = () => apiRequest<GithubProvider[]>(`${ROOT}/github/providers`);
export const deleteProvider = (id: string) =>
  apiRequest<void>(`${ROOT}/github/providers/${encodeURIComponent(id)}`, { method: "DELETE" });
export const listInstallations = () => apiRequest<GithubInstallation[]>(`${ROOT}/github/installations`);
export const syncInstallation = (id: string, key: string) => apiRequest<OperationSummary>(
  `${ROOT}/github/installations/${encodeURIComponent(id)}/sync`, operationOptions("POST", key, {}),
);
export const listInstallationRepositories = (installationId: string) => apiRequest<GithubRepository[]>(
  `${ROOT}/github/installations/${encodeURIComponent(installationId)}/repositories`,
);

export const listApplications = (platform = false) => apiRequest<Application[]>(
  `${ROOT}/applications${platform ? "?scope=all" : ""}`,
);
export const getApplication = (id: string) =>
  apiRequest<ApplicationDetail>(`${ROOT}/applications/${encodeURIComponent(id)}`);
export const createApplication = (body: ApplicationCreate) => apiRequest<Application>(`${ROOT}/applications`, {
  method: "POST", body: JSON.stringify(body),
});
export const updateApplicationSource = (
  id: string,
  installationId: string,
  repositoryId: string,
  branch: string | null,
) => apiRequest<Application>(`${ROOT}/applications/${encodeURIComponent(id)}/source`, {
  method: "PATCH",
  body: JSON.stringify({
    installation_id: installationId, repository_id: repositoryId, branch,
  }),
});
export const inspectApplication = (id: string, key: string) => apiRequest<OperationSummary>(
  `${ROOT}/applications/${encodeURIComponent(id)}/inspect`, operationOptions("POST", key),
);
export const deployApplication = (id: string, environment: EnvironmentEntry[], key: string) =>
  apiRequest<OperationSummary>(`${ROOT}/applications/${encodeURIComponent(id)}/deployments`,
    operationOptions("POST", key, { environment: environment.map(({ name, value }) => [name, value]) }));
export const runApplicationAction = (id: string, action: AppAction, key: string) =>
  apiRequest<OperationSummary>(`${ROOT}/applications/${encodeURIComponent(id)}/actions/${action}`,
    operationOptions("POST", key));
export const runServiceAction = (id: string, service: string, action: Exclude<AppAction, "down">, key: string) =>
  apiRequest<OperationSummary>(`${ROOT}/applications/${encodeURIComponent(id)}/actions/${action}`,
    operationOptions("POST", key, { service }));
export const revealManagedSecret = (id: string, name: string, reason: string) =>
  apiRequest<{ name: string; value: string }>(
    `${ROOT}/applications/${encodeURIComponent(id)}/secrets/${encodeURIComponent(name)}/reveal`,
    { method: "POST", body: JSON.stringify({ reason }) },
  );
export const deleteApplication = (id: string, confirm: string, purgeData: boolean, key: string) =>
  apiRequest<OperationSummary>(`${ROOT}/applications/${encodeURIComponent(id)}`,
    operationOptions("DELETE", key, { confirm, purge_data: purgeData }));
export const listDeployments = (id: string) => apiRequest<DeploymentDetail[]>(
  `${ROOT}/applications/${encodeURIComponent(id)}/deployments`,
);
export const listOperations = (id: string) => apiRequest<Operation[]>(
  `${ROOT}/applications/${encodeURIComponent(id)}/operations`,
);
export const getOperation = (id: string) => apiRequest<Operation>(`${ROOT}/operations/${encodeURIComponent(id)}`);
