import type { PluginFrontendRegistration } from "@systutor/sdk/frontend";

import DomainsPage from "./pages/DomainsPage";

export function registerPlugin(): PluginFrontendRegistration {
  return {
    pluginId: "domains",
    routes: [
      {
        path: "domains",
        title: "Dominios",
        component: DomainsPage,
        requiredPermissions: ["domains.domains.read"],
      },
    ],
    navigation: [
      {
        to: "/app/domains",
        label: "Dominios",
        requiredPermissions: ["domains.domains.read"],
        group: "Administración",
      },
    ],
    widgets: [],
  };
}
