import type { PluginFrontendRegistration } from "@systutor/sdk/frontend";

export function registerPlugin(): PluginFrontendRegistration {
  return { pluginId: "apps", routes: [], navigation: [], widgets: [] };
}
