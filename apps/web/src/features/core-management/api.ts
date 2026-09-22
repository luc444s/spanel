import type { QueryClient } from "@tanstack/react-query";

import { apiRequest } from "@systutor/shell/api/client";
import type { PluginRuntimeRecord } from "@systutor/shell/api/client";

export type CoreUser = {
  id: string;
  tenant_id: string;
  branch_id: string | null;
  name: string;
  email: string;
  active: boolean;
  category: string | null;
  roles: string[];
  created_at: string;
  updated_at: string;
};

export type CoreRole = {
  id: string;
  tenant_id: string;
  name: string;
  permissions: string[];
  active: boolean;
  created_at: string;
  updated_at: string;
};

export type CoreBranch = {
  id: string;
  tenant_id: string;
  name: string;
  code: string;
  active: boolean;
  created_at: string;
  updated_at: string;
};

export type CorePermission = {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
};

export type CreateCoreUserPayload = {
  name: string;
  email: string;
  password: string;
  branch_id: string | null;
  role_ids: string[];
};

export type UpdateCoreUserPayload = {
  name?: string;
  email?: string;
  password?: string;
  branch_id?: string | null;
  role_ids?: string[];
};

export type CoreUserCategory = {
  value: string;
  label: string;
};

export type CreateCoreRolePayload = {
  name: string;
  permission_names: string[];
};

export type UpdateCoreRolePayload = {
  name?: string;
  permission_names?: string[];
};

export type CreateCoreBranchPayload = {
  name: string;
  code: string;
};

export type UpdateCoreBranchPayload = {
  name?: string;
  code?: string;
};

export type MigratePluginPayload = {
  target_revision?: string | null;
};

export const coreManagementKeys = {
  users: ["core-management", "users"] as const,
  roles: ["core-management", "roles"] as const,
  branches: ["core-management", "branches"] as const,
  permissions: ["core-management", "permissions"] as const,
  plugins: ["core-management", "plugins"] as const,
};

export async function invalidateCoreManagementKey(queryClient: QueryClient, key: readonly string[]) {
  await queryClient.invalidateQueries({ queryKey: [...key] });
}

export async function invalidatePluginRuntimeCaches(queryClient: QueryClient) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: [...coreManagementKeys.plugins] }),
    queryClient.invalidateQueries({ queryKey: ["system", "plugin-runtime"] }),
  ]);
}

export function listCoreUsers() {
  return apiRequest<CoreUser[]>("/api/v1/core/users");
}

export function listCoreUserCategories() {
  return apiRequest<CoreUserCategory[]>("/api/v1/core/users/categories");
}

export function createCoreUser(payload: CreateCoreUserPayload) {
  return apiRequest<CoreUser>("/api/v1/core/users", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateCoreUser(userId: string, payload: UpdateCoreUserPayload) {
  return apiRequest<CoreUser>(`/api/v1/core/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function disableCoreUser(userId: string) {
  return apiRequest<CoreUser>(`/api/v1/core/users/${userId}/disable`, { method: "POST" });
}

export function enableCoreUser(userId: string) {
  return apiRequest<CoreUser>(`/api/v1/core/users/${userId}/enable`, { method: "POST" });
}

export function listCoreRoles() {
  return apiRequest<CoreRole[]>("/api/v1/core/roles");
}

export function createCoreRole(payload: CreateCoreRolePayload) {
  return apiRequest<CoreRole>("/api/v1/core/roles", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateCoreRole(roleId: string, payload: UpdateCoreRolePayload) {
  return apiRequest<CoreRole>(`/api/v1/core/roles/${roleId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function disableCoreRole(roleId: string) {
  return apiRequest<CoreRole>(`/api/v1/core/roles/${roleId}/disable`, { method: "POST" });
}

export function enableCoreRole(roleId: string) {
  return apiRequest<CoreRole>(`/api/v1/core/roles/${roleId}/enable`, { method: "POST" });
}

export function listCoreBranches() {
  return apiRequest<CoreBranch[]>("/api/v1/core/branches");
}

export function createCoreBranch(payload: CreateCoreBranchPayload) {
  return apiRequest<CoreBranch>("/api/v1/core/branches", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateCoreBranch(branchId: string, payload: UpdateCoreBranchPayload) {
  return apiRequest<CoreBranch>(`/api/v1/core/branches/${branchId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function disableCoreBranch(branchId: string) {
  return apiRequest<CoreBranch>(`/api/v1/core/branches/${branchId}/disable`, { method: "POST" });
}

export function enableCoreBranch(branchId: string) {
  return apiRequest<CoreBranch>(`/api/v1/core/branches/${branchId}/enable`, { method: "POST" });
}

export function listCorePermissions() {
  return apiRequest<CorePermission[]>("/api/v1/core/permissions");
}

export function listManagedPlugins() {
  return apiRequest<PluginRuntimeRecord[]>("/api/v1/core/plugins");
}

export function installManagedPlugin(pluginId: string) {
  return apiRequest<PluginRuntimeRecord>(`/api/v1/core/plugins/${pluginId}/install`, { method: "POST" });
}

export function enableManagedPlugin(pluginId: string) {
  return apiRequest<PluginRuntimeRecord>(`/api/v1/core/plugins/${pluginId}/enable`, { method: "POST" });
}

export function disableManagedPlugin(pluginId: string) {
  return apiRequest<PluginRuntimeRecord>(`/api/v1/core/plugins/${pluginId}/disable`, { method: "POST" });
}

export function uninstallManagedPlugin(pluginId: string) {
  return apiRequest<PluginRuntimeRecord>(`/api/v1/core/plugins/${pluginId}/uninstall`, { method: "POST" });
}

export function migrateManagedPlugin(pluginId: string, payload?: MigratePluginPayload) {
  return apiRequest<PluginRuntimeRecord>(`/api/v1/core/plugins/${pluginId}/migrate`, {
    method: "POST",
    body: JSON.stringify(payload ?? {}),
  });
}
