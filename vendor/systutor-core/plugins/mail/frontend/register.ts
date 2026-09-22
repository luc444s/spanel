import type { PluginFrontendRegistration } from "@systutor/sdk/frontend";

import MailAccountsPage from "./pages/MailAccountsPage";

export function registerPlugin(): PluginFrontendRegistration {
  return {
    pluginId: "mail",
    routes: [
      {
        path: "mail",
        title: "Correos",
        component: MailAccountsPage,
        requiredPermissions: ["mail.accounts.read"],
      },
    ],
    navigation: [
      {
        to: "/app/mail",
        label: "Correos",
        requiredPermissions: ["mail.accounts.read"],
      },
    ],
    widgets: [],
  };
}
