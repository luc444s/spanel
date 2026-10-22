import type { PluginFrontendRegistration } from "@systutor/sdk/frontend";

import HostingPage from "./pages/HostingPage";

export function registerPlugin(): PluginFrontendRegistration {
  return {
    pluginId: "hosting",
    routes: [
      {
        path: "hosting",
        title: "Hosting",
        component: HostingPage,
        requiredPermissions: ["hosting.sites.read"],
      },
    ],
    navigation: [
      {
        to: "/app/hosting",
        label: "Hosting",
        requiredPermissions: ["hosting.sites.read"],
        group: "Administración",
      },
    ],
    widgets: [],
  };
}
