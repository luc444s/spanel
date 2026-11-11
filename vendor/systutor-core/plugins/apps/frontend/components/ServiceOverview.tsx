import { Button } from "@systutor/shell/ui/button";

import { useT } from "@/i18n/use-i18n";

import type { ServiceDetail } from "../types";

export function ServiceOverview({ service, canManage, pending, updatedAt, onAction }: {
  service: ServiceDetail;
  canManage: boolean;
  pending: boolean;
  updatedAt: number;
  onAction: (action: "refresh" | "logs" | "start" | "stop" | "restart") => void;
}) {
  const t = useT();
  const running = service.status.toLowerCase().includes("running") || service.status.toLowerCase() === "up";
  const seconds = Math.max(0, Math.round((Date.now() - updatedAt) / 1000));
  const lastRevision = seconds < 60
    ? t("{seconds} seconds ago", { seconds })
    : t("{minutes} minutes ago", { minutes: Math.round(seconds / 60) });
  return <div className="space-y-4">
    <dl className="grid gap-3 text-sm sm:grid-cols-2">
      <div><dt className="text-muted-foreground">{t("Status")}</dt><dd>{service.status} · {service.health}</dd></div>
      <div><dt className="text-muted-foreground">{t("Image")}</dt><dd>{service.image ?? "—"}</dd></div>
      <div><dt className="text-muted-foreground">{t("Created")}</dt><dd>{service.created_at ? new Date(service.created_at).toLocaleString() : "—"}</dd></div>
      <div><dt className="text-muted-foreground">{t("Restarts")}</dt><dd>{service.restart_count}</dd></div>
      <div><dt className="text-muted-foreground">{t("Internal ports")}</dt><dd>{service.ports.map((port) => `${port}/tcp`).join(", ") || "—"}</dd></div>
      <div><dt className="text-muted-foreground">{t("Last revision")}</dt><dd>{lastRevision}</dd></div>
    </dl>
    {canManage ? <div className="flex flex-wrap gap-2">
      <Button variant="secondary" disabled={pending} onClick={() => onAction("refresh")}>{t("Refresh")}</Button>
      <Button variant="secondary" disabled={pending} onClick={() => onAction("logs")}>{t("Request logs")}</Button>
      <Button variant="secondary" disabled={pending} onClick={() => onAction("restart")}>{t("Restart")}</Button>
      <Button variant="secondary" disabled={pending} onClick={() => onAction(running ? "stop" : "start")}>{running ? t("Stop") : t("Start")}</Button>
    </div> : null}
  </div>;
}
