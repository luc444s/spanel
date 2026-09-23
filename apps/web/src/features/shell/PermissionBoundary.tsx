import { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { useAuthStore } from "../auth/store";
import { hasAnyPermission, hasRequiredPermissions } from "./permissions";

type PermissionBoundaryProps = {
  requiredPermissions?: string[];
  anyPermissions?: string[];
  children: ReactNode;
};

export function PermissionBoundary({
  requiredPermissions,
  anyPermissions,
  children,
}: PermissionBoundaryProps) {
  const permissions = useAuthStore((state) => state.permissions);
  const isSuperadmin = useAuthStore((state) => state.isSuperadmin);

  const hasRequired = hasRequiredPermissions(permissions, requiredPermissions, isSuperadmin);
  const hasAny = hasAnyPermission(permissions, anyPermissions, isSuperadmin);

  if (!hasRequired || !hasAny) {
    return <Navigate replace to="/app/dashboard" />;
  }

  return <>{children}</>;
}
