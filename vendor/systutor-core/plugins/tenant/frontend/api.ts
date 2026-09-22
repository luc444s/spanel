import { apiRequest } from "@systutor/shell/api/client";

export type TenantResponse = {
  id: string;
  name: string;
  slug: string;
  domain: string | null;
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

export type CreateTenantPayload = {
  name: string;
  slug: string;
  domain?: string;
  user_email: string;
  user_password: string;
  user_full_name?: string;
};

export type UpdateTenantDomainPayload = {
  domain: string | null;
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

export function updateTenantDomain(tenantId: string, payload: UpdateTenantDomainPayload) {
  return apiRequest<TenantResponse>(`/api/v1/plugins/tenant/tenants/${tenantId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function listTenantUsers(tenantId: string) {
  return apiRequest<TenantUsersResponse>(`/api/v1/plugins/tenant/tenants/${tenantId}/users`);
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
