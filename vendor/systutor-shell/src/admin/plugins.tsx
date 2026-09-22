import { useEffect, useState } from "react";
import { apiRequest } from "../api/client";
import type { PluginRuntimeRecord } from "../api/client";
import { Alert } from "../ui/alert";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";

const ACTIONS_BY_STATE: Record<string, string[]> = {
  discovered: ["install"],
  installed: ["enable"],
  enabled: ["disable", "migrate", "uninstall"],
  disabled: ["enable", "uninstall"],
  failed: ["install"],
  uninstalled: ["install"],
};

const ACTION_LABELS: Record<string, string> = {
  install: "Instalar",
  enable: "Habilitar",
  disable: "Deshabilitar",
  migrate: "Migrar",
  uninstall: "Desinstalar",
};

export function PluginsView() {
  const [plugins, setPlugins] = useState<PluginRuntimeRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = async () => {
    try {
      setPlugins(await apiRequest<PluginRuntimeRecord[]>("/api/v1/core/plugins"));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const run = async (pluginId: string, action: string) => {
    setBusy(pluginId);
    setError(null);
    try {
      await apiRequest(`/api/v1/core/plugins/${pluginId}/${action}`, { method: "POST" });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Plugins</CardTitle>
        <CardDescription>Ciclo de vida del plugin runtime</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <Alert title="Error">{error}</Alert>}
        {loading ? (
          <p className="text-sm text-muted-foreground">Cargando…</p>
        ) : plugins.length === 0 ? (
          <p className="text-sm text-muted-foreground">Sin plugins descubiertos.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="py-2 pr-4 font-medium">Plugin</th>
                  <th className="py-2 pr-4 font-medium">Versión</th>
                  <th className="py-2 pr-4 font-medium">Estado</th>
                  <th className="py-2 font-medium">Acciones</th>
                </tr>
              </thead>
              <tbody>
                {plugins.map((plugin) => (
                  <tr key={plugin.id} className="border-b border-border/50">
                    <td className="py-2 pr-4">
                      <div className="font-medium">{plugin.name}</div>
                      <div className="text-xs text-muted-foreground">{plugin.plugin_id}</div>
                    </td>
                    <td className="py-2 pr-4 text-muted-foreground">v{plugin.version}</td>
                    <td className="py-2 pr-4">
                      <Badge>{plugin.is_enabled ? "enabled" : plugin.state}</Badge>
                    </td>
                    <td className="py-2">
                      <div className="flex flex-wrap gap-2">
                        {(ACTIONS_BY_STATE[plugin.state] ?? []).map((action) => (
                          <Button
                            key={action}
                            variant="secondary"
                            disabled={busy === plugin.id}
                            onClick={() => void run(plugin.plugin_id, action)}
                          >
                            {busy === plugin.id ? "…" : ACTION_LABELS[action] ?? action}
                          </Button>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
