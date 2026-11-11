import type { PluginFrontendRegistration } from "@systutor/sdk/frontend";
import FileManagerPage from "./FileManagerPage";

export function registerPlugin(): PluginFrontendRegistration {
  return {
    pluginId: "filemanager",
    routes: [{
      path: "filemanager/:provider/:resourceId",
      title: "File manager",
      component: FileManagerPage,
      requiredPermissions: ["filemanager.files.read"],
    }],
    navigation: [],
    widgets: [],
  };
}
