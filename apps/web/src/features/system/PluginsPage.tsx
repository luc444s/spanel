import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  coreManagementKeys,
  disableManagedPlugin,
  enableManagedPlugin,
  installManagedPlugin,
  invalidatePluginRuntimeCaches,
  listManagedPlugins,
  migrateManagedPlugin,
  uninstallManagedPlugin,
} from "../core-management/api";
import { useAuthStore } from "../auth/store";
import { t } from "@/i18n";
import { pluginDescription, pluginName } from "@/i18n/plugin-metadata";
import { useT } from "@/i18n/use-i18n";
import { Alert } from "@systutor/shell/ui/alert";
import { Badge } from "@systutor/shell/ui/badge";
import { Button } from "@systutor/shell/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@systutor/shell/ui/card";
import { DataTable } from "@systutor/shell/ui/data-table";
import type { PluginRuntimeRecord } from "@systutor/shell/api/client";

export function PluginsPage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((state) => state.permissions);
  const canManage = permissions.includes("core.plugin.manage");

  const pluginsQuery = useQuery({
    queryKey: [...coreManagementKeys.plugins],
    queryFn: listManagedPlugins,
  });

  const pluginActionMutation = useMutation({
    mutationFn: async ({ plugin, action }: { plugin: PluginRuntimeRecord; action: PluginAction }) => {
      switch (action) {
        case "install":
          return installManagedPlugin(plugin.plugin_id);
        case "enable":
          return enableManagedPlugin(plugin.plugin_id);
        case "disable":
          return disableManagedPlugin(plugin.plugin_id);
        case "uninstall":
          return uninstallManagedPlugin(plugin.plugin_id);
        case "migrate":
          return migrateManagedPlugin(plugin.plugin_id);
      }
    },
    onSuccess: async () => {
      await invalidatePluginRuntimeCaches(queryClient);
    },
  });

  return (
    <PluginsPageContent
      plugins={pluginsQuery.data ?? []}
      canManage={canManage}
      hasError={Boolean(pluginsQuery.error)}
      isMutating={pluginActionMutation.isPending}
      onAction={(plugin, action) => pluginActionMutation.mutate({ plugin, action })}
    />
  );
}

type PluginAction = "install" | "enable" | "disable" | "uninstall" | "migrate";

type PluginsPageContentProps = {
  plugins: PluginRuntimeRecord[];
  canManage: boolean;
  hasError: boolean;
  isMutating: boolean;
  onAction: (plugin: PluginRuntimeRecord, action: PluginAction) => void;
};

export function PluginsPageContent({ plugins, canManage, hasError, isMutating, onAction }: PluginsPageContentProps) {
  const t = useT();

  return (
    <section className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold text-foreground">{t("Plugins")}</h1>
        <p className="text-sm text-muted-foreground">
          {t("Minimal management of the persistent runtime, including lifecycle, errors, and frontend visibility.")}
        </p>
      </div>

      {hasError ? (
        <Alert title={t("Could not load the plugin runtime")}>
          {t("Check permissions or backend availability.")}
        </Alert>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>{t("Plugin runtime")}</CardTitle>
          <CardDescription>
            {t("Technical status and administrative actions for the persistent runtime.")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <DataTable
            columns={[
              { key: "plugin_id", header: t("ID"), render: (plugin) => plugin.plugin_id },
              { key: "version", header: t("Version"), render: (plugin) => plugin.version },
              { key: "api_version", header: t("API Version"), render: (plugin) => plugin.api_version },
              { key: "state", header: t("Status"), render: (plugin) => <Badge>{plugin.state}</Badge> },
              { key: "installed_at", header: t("Installed"), render: (plugin) => plugin.installed_at ?? "-" },
              { key: "enabled_at", header: t("Enabled"), render: (plugin) => plugin.enabled_at ?? "-" },
              {
                key: "last_error",
                header: t("Last error"),
                render: (plugin) => plugin.last_error ?? "-",
              },
              {
                key: "actions",
                header: t("Actions"),
                className: "w-72",
                render: (plugin) => (
                  <div className="flex flex-wrap gap-2">
                    {canManage ? renderPluginActions(plugin, isMutating, onAction) : <span className="text-muted-foreground">{t("Read only")}</span>}
                  </div>
                ),
              },
            ]}
            rows={plugins}
            rowKey={(plugin) => plugin.id}
            emptyMessage={t("No plugins registered in the runtime.")}
          />
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        {plugins.map((plugin) => (
          <Card key={plugin.id}>
            <CardHeader>
              <div className="flex items-center justify-between gap-3">
                <CardTitle>{t(pluginName(plugin.plugin_id, plugin.name))}</CardTitle>
                <div className="flex items-center gap-2">
                  <Badge>{plugin.version}</Badge>
                  <Badge>{plugin.state}</Badge>
                </div>
              </div>
              <CardDescription>{t(pluginDescription(plugin.plugin_id, plugin.description))}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4 text-sm text-muted-foreground">
              <InfoLine label={t("Plugin ID")} value={plugin.plugin_id} />
              <InfoLine label={t("Enabled")} value={plugin.is_enabled ? t("Yes") : t("No")} />
              {plugin.last_error ? <Alert title={t("Last error")}>{plugin.last_error}</Alert> : null}
            </CardContent>
          </Card>
        ))}
      </div>
    </section>
  );
}

function renderPluginActions(
  plugin: PluginRuntimeRecord,
  isMutating: boolean,
  onAction: (plugin: PluginRuntimeRecord, action: PluginAction) => void
) {
  const actions: PluginAction[] = [];

  if (plugin.state !== "enabled") {
    actions.push("install");
  }
  if (plugin.state !== "enabled") {
    actions.push("enable");
  }
  if (plugin.state === "enabled") {
    actions.push("disable");
  }
  if (plugin.state !== "enabled") {
    actions.push("uninstall");
  }
  actions.push("migrate");

  return actions.map((action) => (
    <Button key={action} variant="secondary" disabled={isMutating} onClick={() => onAction(plugin, action)}>
      {actionLabel(action)}
    </Button>
  ));
}

function actionLabel(action: PluginAction) {
  switch (action) {
    case "install":
      return t("Install");
    case "enable":
      return t("Enable");
    case "disable":
      return t("Disable");
    case "uninstall":
      return t("Uninstall");
    case "migrate":
      return t("Migrate");
  }
}

function InfoLine({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="break-all text-foreground">{value ?? "-"}</span>
    </div>
  );
}
