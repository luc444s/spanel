import type { PluginFrontendRegistration } from "@systutor/sdk/frontend";

import { t } from "@/i18n";
import MailAccountsPage from "./pages/MailAccountsPage";

export function registerPlugin(): PluginFrontendRegistration {
  return {
    pluginId: "mail",
    routes: [
      {
        path: "mail",
        title: t("Mail"),
        component: MailAccountsPage,
        requiredPermissions: ["mail.accounts.read"],
      },
    ],
    navigation: [
      {
        to: "/app/mail",
        label: t("Mail"),
        requiredPermissions: ["mail.accounts.read"],
      },
    ],
    widgets: [],
  };
}
