import { useStore, type UseBoundStore } from "zustand/react";
import { createStore, type StoreApi } from "zustand/vanilla";

import { setTokenProvider } from "@systutor/shell/api/client";
import type { UserProfile } from "./api";
import type { PluginRuntimeRecord } from "@systutor/shell/api/client";

const TOKEN_KEY = "systutor.access_token";

export type TenantContext = {
  id: string;
  name: string;
};

export type BranchContext = {
  id: string | null;
  name: string | null;
};

function readStoredToken() {
  if (typeof window === "undefined") {
    return null;
  }

  return window.localStorage.getItem(TOKEN_KEY);
}

type AuthState = {
  token: string | null;
  user: UserProfile | null;
  currentTenant: TenantContext | null;
  currentBranch: BranchContext | null;
  permissions: string[];
  enabledPlugins: PluginRuntimeRecord[];
  pluginRuntimeRecords: PluginRuntimeRecord[];
  isRuntimeBootstrapped: boolean;
  isSuperadmin: boolean;
  setSession: (token: string) => void;
  hydrateUserContext: (user: UserProfile | null) => void;
  setPluginRuntime: (records: PluginRuntimeRecord[]) => void;
  logout: () => void;
};

const authStore = createStore<AuthState>((set) => ({
  token: readStoredToken(),
  user: null,
  currentTenant: null,
  currentBranch: null,
  permissions: [],
  enabledPlugins: [],
  pluginRuntimeRecords: [],
  isRuntimeBootstrapped: false,
  isSuperadmin: false,
  setSession: (token) => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(TOKEN_KEY, token);
    }

    set((state) => ({ ...initialAuthContextState, token }));
  },
  hydrateUserContext: (user) => {
    set((state) => {
      if (isSameUserProfile(state.user, user)) {
        return state;
      }

      return {
        ...state,
        user,
        currentTenant: user
          ? {
              id: user.tenant_id,
              name: user.tenant_name,
            }
          : null,
        currentBranch: user
          ? {
              id: user.branch_id,
              name: user.branch_name,
            }
          : null,
        permissions: user?.permissions ?? [],
        isSuperadmin: user?.is_superadmin ?? false,
      };
    });
  },
  setPluginRuntime: (records) => {
    set((state) => {
      if (isSamePluginRuntimeRecords(state.pluginRuntimeRecords, records)) {
        return state;
      }

      return {
        ...state,
        pluginRuntimeRecords: records,
        enabledPlugins: records.filter((record) => record.state === "enabled" && record.is_enabled),
        isRuntimeBootstrapped: true,
      };
    });
  },
  logout: () => {
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(TOKEN_KEY);
    }

    set({ ...initialAuthContextState, token: null });
  },
}));

export const useAuthStore = Object.assign(
  <T>(selector: (state: AuthState) => T) => useStore(authStore, selector),
  authStore
) as UseBoundStore<StoreApi<AuthState>>;

const initialAuthContextState = {
  user: null,
  currentTenant: null,
  currentBranch: null,
  permissions: [],
  enabledPlugins: [],
  pluginRuntimeRecords: [],
  isRuntimeBootstrapped: false,
  isSuperadmin: false,
} satisfies Omit<AuthState, "token" | "setSession" | "hydrateUserContext" | "setPluginRuntime" | "logout">;

function isSameUserProfile(left: UserProfile | null, right: UserProfile | null) {
  if (left === right) {
    return true;
  }

  if (left === null || right === null) {
    return false;
  }

  return (
    left.id === right.id &&
    left.tenant_id === right.tenant_id &&
    left.tenant_name === right.tenant_name &&
    left.branch_id === right.branch_id &&
    left.branch_name === right.branch_name &&
    left.email === right.email &&
    left.full_name === right.full_name &&
    left.is_active === right.is_active &&
    left.is_superadmin === right.is_superadmin &&
    left.permissions.length === right.permissions.length &&
    left.permissions.every((permission, index) => permission === right.permissions[index])
  );
}

function isSamePluginRuntimeRecords(
  left: PluginRuntimeRecord[],
  right: PluginRuntimeRecord[]
) {
  if (left === right) {
    return true;
  }

  if (left.length !== right.length) {
    return false;
  }

  return left.every((record, index) => {
    const candidate = right[index];

    return (
      record.id === candidate.id &&
      record.plugin_id === candidate.plugin_id &&
      record.state === candidate.state &&
      record.is_enabled === candidate.is_enabled &&
      record.updated_at === candidate.updated_at
    );
  });
}

setTokenProvider(() => useAuthStore.getState().token);
