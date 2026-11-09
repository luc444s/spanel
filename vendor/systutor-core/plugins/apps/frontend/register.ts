import type { PluginFrontendRegistration } from "@systutor/sdk/frontend";

import AppsPage from "./pages/AppsPage";

export function registerPlugin(): PluginFrontendRegistration {
  return {
    pluginId: "apps",
    routes: [{
      path: "apps", title: "Apps", component: AppsPage,
      requiredPermissions: ["apps.applications.read"],
    }],
    navigation: [{
      to: "/app/apps", label: "Apps", group: "Administración",
      requiredPermissions: ["apps.applications.read"],
    }],
    widgets: [],
  };
}
