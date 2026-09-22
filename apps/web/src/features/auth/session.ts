import type { QueryClient } from "@tanstack/react-query";

import type { UserProfile } from "./api";
import { useAuthStore } from "./store";

export function hasValidTenantContext(user: UserProfile | null) {
  if (!user) {
    return false;
  }

  if (!user.tenant_id || !user.tenant_name.trim()) {
    return false;
  }

  if (user.branch_id !== null && (!user.branch_name || !user.branch_name.trim())) {
    return false;
  }

  return true;
}

export function clearClientSession(queryClient: QueryClient) {
  useAuthStore.getState().logout();
  queryClient.clear();
}
