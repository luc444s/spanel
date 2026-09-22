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
  return (
    <section className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold text-foreground">Plugins</h1>
        <p className="text-sm text-muted-foreground">
          Management mínimo del runtime persistente, incluyendo lifecycle, errores y visibilidad frontend.
        </p>
      </div>

      {hasError ? (
        <Alert title="No se pudo cargar el runtime de plugins">Revisa permisos o disponibilidad del backend.</Alert>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Plugin runtime</CardTitle>
          <CardDescription>Estado técnico y acciones administrativas del runtime persistente.</CardDescription>
        </CardHeader>
        <CardContent>
          <DataTable
            columns={[
              { key: "plugin_id", header: "ID", render: (plugin) => plugin.plugin_id },
              { key: "version", header: "Versión", render: (plugin) => plugin.version },
              { key: "api_version", header: "API Versión", render: (plugin) => plugin.api_version },
              { key: "state", header: "Estado", render: (plugin) => <Badge>{plugin.state}</Badge> },
              { key: "installed_at", header: "Instalado", render: (plugin) => plugin.installed_at ?? "-" },
              { key: "enabled_at", header: "Activado", render: (plugin) => plugin.enabled_at ?? "-" },
              {
                key: "last_error",
                header: "Último error",
                render: (plugin) => plugin.last_error ?? "-",
              },
              {
                key: "actions",
                header: "Acciones",
                className: "w-72",
                render: (plugin) => (
                  <div className="flex flex-wrap gap-2">
                    {canManage ? renderPluginActions(plugin, isMutating, onAction) : <span className="text-muted-foreground">Solo lectura</span>}
                  </div>
                ),
              },
            ]}
            rows={plugins}
            rowKey={(plugin) => plugin.id}
            emptyMessage="No hay plugins registrados en el runtime."
          />
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        {plugins.map((plugin) => (
          <Card key={plugin.id}>
            <CardHeader>
              <div className="flex items-center justify-between gap-3">
                <CardTitle>{plugin.name}</CardTitle>
                <div className="flex items-center gap-2">
                  <Badge>{plugin.version}</Badge>
                  <Badge>{plugin.state}</Badge>
                </div>
              </div>
              <CardDescription>{plugin.description}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4 text-sm text-muted-foreground">
              <InfoLine label="Plugin ID" value={plugin.plugin_id} />
              <InfoLine label="Enabled" value={plugin.is_enabled ? "si" : "no"} />
              {plugin.last_error ? <Alert title="Ultimo error">{plugin.last_error}</Alert> : null}
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
      return "Instalar";
    case "enable":
      return "Activar";
    case "disable":
      return "Desactivar";
    case "uninstall":
      return "Desinstalar";
    case "migrate":
      return "Migrar";
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
