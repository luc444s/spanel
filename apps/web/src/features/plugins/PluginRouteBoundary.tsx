import { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { useAuthStore } from "../auth/store";
import { hasRequiredPermissions } from "../shell/permissions";
import { usePluginFrontendRuntime } from "./runtime";
import { Alert } from "@systutor/shell/ui/alert";

const EMPTY_PERMISSIONS: string[] = [];

type PluginRouteBoundaryProps = {
  pluginId: string;
  requiredPermissions?: string[];
  children: ReactNode;
};

export function PluginRouteBoundary({
  pluginId,
  requiredPermissions,
  children,
}: PluginRouteBoundaryProps) {
  const runtime = usePluginFrontendRuntime();
  const userPermissions = useAuthStore((state) => state.permissions ?? EMPTY_PERMISSIONS);
  const record = runtime.recordsById.get(pluginId);

  if (runtime.isLoading) {
    return <div className="text-sm text-slate-400">Cargando runtime del plugin...</div>;
  }

  if (runtime.error) {
    return (
      <Alert title="No se pudo validar el runtime del plugin">
        Revisa el backend o los permisos del usuario actual.
      </Alert>
    );
  }

  if (!record || record.state !== "enabled" || !record.is_enabled) {
    return <Navigate replace to="/app/plugins" />;
  }

  if (!hasRequiredPermissions(userPermissions, requiredPermissions)) {
    return <Navigate replace to="/app/plugins" />;
  }

  return <>{children}</>;
}
