import { createBrowserRouter, Navigate } from "react-router-dom";

import { LoginPage } from "../features/auth/LoginPage";
import { PluginRouteBoundary } from "../features/plugins/PluginRouteBoundary";
import { listFrontendPluginRegistrations } from "../features/plugins/runtime";
import { RequireAuth } from "../features/auth/RequireAuth";
import { SystemDashboardPage } from "../features/system/SystemDashboardPage";
import { PluginsPage } from "../features/system/PluginsPage";
import { UsersPage } from "../features/settings/UsersPage";
import { RolesPage } from "../features/settings/RolesPage";
import { BranchesPage } from "../features/settings/BranchesPage";
import { PermissionBoundary } from "../features/shell/PermissionBoundary";
import { AppLayout } from "@/shared/layout/AppLayout";
import { useAuthStore } from "../features/auth/store";

function RootRedirect() {
  const token = useAuthStore((state) => state.token);
  return <Navigate replace to={token ? "/app/dashboard" : "/login"} />;
}

const pluginRoutes = listFrontendPluginRegistrations().flatMap((registration) =>
  registration.routes.map((route) => {
    const RouteComponent = route.component;

    return {
      path: route.path,
      element: (
        <PluginRouteBoundary
          pluginId={registration.pluginId}
          requiredPermissions={route.requiredPermissions}
        >
          <RouteComponent />
        </PluginRouteBoundary>
      ),
    };
  })
);

export const appRouter = createBrowserRouter([
  {
    path: "/",
    element: <RootRedirect />,
  },
  {
    path: "/login",
    element: <LoginPage />,
  },
  {
    path: "/app",
    element: <RequireAuth />,
    children: [
      {
        element: <AppLayout />,
        children: [
          {
            index: true,
            element: <Navigate replace to="dashboard" />,
          },
          {
            path: "dashboard",
            element: <SystemDashboardPage />,
          },
          {
            path: "plugins",
            element: (
              <PermissionBoundary anyPermissions={["core.plugin.runtime.read", "core.plugin.manage"]}>
                <PluginsPage />
              </PermissionBoundary>
            ),
          },
          {
            path: "settings/users",
            element: (
              <PermissionBoundary requiredPermissions={["core.users.read"]}>
                <UsersPage />
              </PermissionBoundary>
            ),
          },
          {
            path: "settings/roles",
            element: (
              <PermissionBoundary anyPermissions={["core.roles.read", "core.roles.manage"]}>
                <RolesPage />
              </PermissionBoundary>
            ),
          },
          {
            path: "settings/branches",
            element: (
              <PermissionBoundary anyPermissions={["core.branches.read", "core.branches.manage"]}>
                <BranchesPage />
              </PermissionBoundary>
            ),
          },
          ...pluginRoutes,
        ],
      },
    ],
  },
]);
