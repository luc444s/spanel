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
  warehouse_ids: string[];
};

export type LoginResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserProfile;
};

export type CoreRoleRead = {
  id: string;
  tenant_id: string;
  name: string;
  permissions: string[];
  active: boolean;
  created_at: string;
  updated_at: string;
};

export type CorePermissionRead = {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
};

export type CoreUserCategoryRead = {
  value: string;
  label: string;
};

export type CoreUserRead = {
  id: string;
  tenant_id: string;
  branch_id: string | null;
  name: string;
  email: string;
  active: boolean;
  category: string | null;
  roles: string[];
  warehouse_ids: string[];
  created_at: string;
  updated_at: string;
};

export type CoreBranchRead = {
  id: string;
  tenant_id: string;
  name: string;
  code: string;
  active: boolean;
  created_at: string;
  updated_at: string;
};
