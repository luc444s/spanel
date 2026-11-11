import type { PluginFrontendRegistration } from "@systutor/sdk/frontend";

import DomainsPage from "./pages/DomainsPage";

export function registerPlugin(): PluginFrontendRegistration {
  return {
    pluginId: "domains",
    routes: [
      {
        path: "domains",
        title: "Domains",
        component: DomainsPage,
        requiredPermissions: ["domains.domains.read"],
      },
    ],
    navigation: [
      {
        to: "/app/domains",
        label: "Domains",
        requiredPermissions: ["domains.domains.read"],
        group: "Administration",
      },
    ],
    widgets: [],
  };
}
