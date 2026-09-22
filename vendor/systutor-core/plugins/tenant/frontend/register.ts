import type { PluginFrontendRegistration } from "@systutor/sdk/frontend";

import TenantsPage from "./pages/TenantsPage";

export function registerPlugin(): PluginFrontendRegistration {
  return {
    pluginId: "tenant",
    routes: [
      {
        path: "tenants",
        title: "Tenants",
        component: TenantsPage,
        requiredPermissions: ["tenant.tenants.read"],
      },
    ],
    navigation: [
      {
        to: "/app/tenants",
        label: "Tenants",
        requiredPermissions: ["tenant.tenants.read"],
        group: "Administración",
      },
    ],
    widgets: [],
  };
}
