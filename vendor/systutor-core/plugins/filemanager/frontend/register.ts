import type { PluginFrontendRegistration } from "@systutor/sdk/frontend";
import FileManagerPage from "./FileManagerPage";

export function registerPlugin(): PluginFrontendRegistration {
  return {
    pluginId: "filemanager",
    routes: [{
      path: "filemanager/:provider/:resourceId",
      title: "Gestor de archivos",
      component: FileManagerPage,
      requiredPermissions: ["filemanager.files.read"],
    }],
    navigation: [],
    widgets: [],
  };
}
