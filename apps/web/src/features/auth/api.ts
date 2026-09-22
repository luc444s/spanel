import { apiRequest } from "@systutor/shell/api/client";

export type UserProfile = {
  id: string;
  tenant_id: string;
  tenant_name: string;
  branch_id: string | null;
  branch_name: string | null;
  email: string;
  full_name: string;
  is_active: boolean;
  is_superadmin: boolean;
  category: string | null;
  permissions: string[];
};

export type LoginRequest = {
  email: string;
  password: string;
};

export type LoginResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserProfile;
};

export function login(payload: LoginRequest) {
  return apiRequest<LoginResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getCurrentUser() {
  return apiRequest<UserProfile>("/api/v1/auth/me");
}
