import { useQuery } from "@tanstack/react-query";

import { usePluginFrontendRuntime } from "../plugins/runtime";
import { getSystemHealth, getSystemReady } from "./api";
import { t } from "@/i18n";
import { useT } from "@/i18n/use-i18n";
import { Alert } from "@systutor/shell/ui/alert";
import { Badge } from "@systutor/shell/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@systutor/shell/ui/card";

export function SystemDashboardPage() {
  const t = useT();
  const healthQuery = useQuery({
    queryKey: ["system", "health"],
    queryFn: getSystemHealth,
  });
  const pluginRuntime = usePluginFrontendRuntime();

  const readyQuery = useQuery({
    queryKey: ["system", "ready"],
    queryFn: getSystemReady,
  });

  const hasError = healthQuery.error || readyQuery.error;

  return (
    <section className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold text-foreground">{t("Dashboard")}</h1>
        <p className="text-sm text-muted-foreground">
          {t("System status, backend health, and connected services.")}
        </p>
      </div>

      {hasError ? (
        <Alert title={t("Could not load system status")}>
          {t("Check that the backend is running and the API is available.")}
        </Alert>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{t("API")}</CardTitle>
            <CardDescription>{t("Basic API status.")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <MetricRow label={t("Status")} value={healthQuery.data?.status ?? t("Loading")} />
            <MetricRow label={t("Service")} value={healthQuery.data?.service ?? "-"} />
            <MetricRow label={t("Version")} value={healthQuery.data?.version ?? "-"} />
            <MetricRow label={t("Environment")} value={healthQuery.data?.env ?? "-"} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("Backend")}</CardTitle>
            <CardDescription>{t("Core connectivity and runtime.")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <MetricRow label={t("Database")} value={boolLabel(readyQuery.data?.database_connected)} />
            <MetricRow label={t("Redis")} value={boolLabel(readyQuery.data?.redis_configured)} />
            <MetricRow label={t("Plugins")} value={String(readyQuery.data?.plugins_loaded ?? 0)} />
            <MetricRow
              label={t("DB configured")}
              value={boolLabel(readyQuery.data?.database_configured)}
            />
          </CardContent>
        </Card>
      </div>

      {pluginRuntime.widgets.length > 0 ? (
        <div className="space-y-3">
          <div className="space-y-1">
            <h2 className="text-lg font-semibold text-foreground">{t("Plugin widgets")}</h2>
            <p className="text-sm text-muted-foreground">
              {t("Components enabled by runtime status and user permissions.")}
            </p>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            {pluginRuntime.widgets.map((widget) => {
              const WidgetComponent = widget.component;

              return (
                <Card key={widget.id}>
                  <CardHeader>
                    <CardTitle>{widget.title}</CardTitle>
                    <CardDescription>{widget.pluginId}</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <WidgetComponent />
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between rounded-md border border-border bg-muted/70 px-3 py-2">
      <span className="text-sm text-muted-foreground">{label}</span>
      <Badge>{value}</Badge>
    </div>
  );
}

function boolLabel(value: boolean | undefined) {
  if (value === undefined) {
    return t("Loading");
  }

  return value ? t("ok") : t("no");
}
