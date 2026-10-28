import { apiRequest } from "@systutor/shell/api/client";

export type TenantResponse = {
  id: string;
  name: string;
  slug: string;
  domain: string | null;
  site_suffix: string | null;
  is_active: boolean;
};

export type TenantListResponse = {
  tenants: TenantResponse[];
};

export type TenantUserResponse = {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  branch_id: string | null;
};

export type TenantUsersResponse = {
  users: TenantUserResponse[];
};

export type TenantDomainItem = {
  domain: string;
};

export type TenantDomainsResponse = {
  domains: TenantDomainItem[];
};

export type CreateTenantPayload = {
  name: string;
  slug: string;
  domain?: string;
  user_email: string;
  user_password: string;
  user_full_name?: string;
};

export type UpdateTenantPayload = {
  domain?: string | null;
  site_suffix?: string | null;
};

export type AssignUserPayload = {
  user_id: string;
};

export type CreateUserInTenantPayload = {
  email: string;
  password: string;
  full_name?: string;
};

export type ReassignUserPayload = {
  target_tenant_id: string;
};

export const tenantKeys = {
  tenants: ["tenant", "tenants"] as const,
  users: (tenantId: string) => ["tenant", tenantId, "users"] as const,
  domains: (tenantId: string) => ["tenant", tenantId, "domains"] as const,
};

export function listTenants() {
  return apiRequest<TenantListResponse>("/api/v1/plugins/tenant/tenants");
}

export function createTenant(payload: CreateTenantPayload) {
  return apiRequest<TenantResponse>("/api/v1/plugins/tenant/tenants", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateTenant(tenantId: string, payload: UpdateTenantPayload) {
  return apiRequest<TenantResponse>(`/api/v1/plugins/tenant/tenants/${tenantId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function listTenantUsers(tenantId: string) {
  return apiRequest<TenantUsersResponse>(`/api/v1/plugins/tenant/tenants/${tenantId}/users`);
}

export function listTenantDomains(tenantId: string) {
  return apiRequest<TenantDomainsResponse>(`/api/v1/plugins/tenant/tenants/${tenantId}/domains`);
}

export function addTenantDomain(tenantId: string, domain: string) {
  return apiRequest<TenantDomainItem>(`/api/v1/plugins/tenant/tenants/${tenantId}/domains`, {
    method: "POST",
    body: JSON.stringify({ domain }),
  });
}

export function removeTenantDomain(tenantId: string, domain: string) {
  return apiRequest<void>(
    `/api/v1/plugins/tenant/tenants/${tenantId}/domains/${encodeURIComponent(domain)}`,
    { method: "DELETE" }
  );
}

export function assignUserToTenant(tenantId: string, payload: AssignUserPayload) {
  return apiRequest<TenantUserResponse>(`/api/v1/plugins/tenant/tenants/${tenantId}/users/assign`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function createUserInTenant(tenantId: string, payload: CreateUserInTenantPayload) {
  return apiRequest<TenantUserResponse>(`/api/v1/plugins/tenant/tenants/${tenantId}/users`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function reassignUserFromTenant(tenantId: string, userId: string, payload: ReassignUserPayload) {
  return apiRequest<TenantUserResponse>(`/api/v1/plugins/tenant/tenants/${tenantId}/users/${userId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}
