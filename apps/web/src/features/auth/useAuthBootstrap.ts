import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { getCurrentUser } from "./api";
import { clearClientSession, hasValidTenantContext } from "./session";
import { useAuthStore } from "./store";
import { ApiError } from "@systutor/shell/api/client";
import { getPluginRuntime } from "../system/api";

export function useAuthBootstrap() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const token = useAuthStore((state) => state.token);
  const permissions = useAuthStore((state) => state.permissions);
  const hydrateUserContext = useAuthStore((state) => state.hydrateUserContext);
  const setPluginRuntime = useAuthStore((state) => state.setPluginRuntime);

  const currentUserQuery = useQuery({
    queryKey: ["auth", "me", token],
    queryFn: getCurrentUser,
    enabled: Boolean(token),
  });

  const effectivePermissions = currentUserQuery.data?.permissions ?? permissions;
  const canReadPluginRuntime =
    effectivePermissions.includes("core.plugin.read") ||
    effectivePermissions.includes("core.plugin.runtime.read") ||
    effectivePermissions.includes("core.plugin.manage");

  const pluginRuntimeQuery = useQuery({
    queryKey: ["system", "plugin-runtime", token],
    queryFn: getPluginRuntime,
    enabled: Boolean(token) && canReadPluginRuntime,
  });

  useEffect(() => {
    if (!currentUserQuery.data) {
      return;
    }

    if (!hasValidTenantContext(currentUserQuery.data)) {
      clearClientSession(queryClient);
      navigate("/login", { replace: true });
      return;
    }

    hydrateUserContext(currentUserQuery.data);
  }, [currentUserQuery.data, hydrateUserContext, navigate, queryClient]);

  useEffect(() => {
    if (currentUserQuery.error instanceof ApiError && currentUserQuery.error.status === 401) {
      clearClientSession(queryClient);
      navigate("/login", { replace: true });
    }
  }, [currentUserQuery.error, navigate, queryClient]);

  useEffect(() => {
    if (pluginRuntimeQuery.data) {
      setPluginRuntime(pluginRuntimeQuery.data);
      return;
    }

    if (pluginRuntimeQuery.error instanceof ApiError) {
      if (pluginRuntimeQuery.error.status === 401) {
        clearClientSession(queryClient);
        navigate("/login", { replace: true });
        return;
      }

      if (pluginRuntimeQuery.error.status === 403) {
        setPluginRuntime([]);
      }
    }
  }, [pluginRuntimeQuery.data, pluginRuntimeQuery.error, navigate, queryClient, setPluginRuntime]);

  useEffect(() => {
    if (!token) {
      hydrateUserContext(null);
      setPluginRuntime([]);
    }
  }, [hydrateUserContext, setPluginRuntime, token]);

  return {
    currentUserQuery,
    pluginRuntimeQuery,
    isLoading:
      Boolean(token) &&
      (currentUserQuery.isPending || (canReadPluginRuntime && pluginRuntimeQuery.isPending)),
  };
}
