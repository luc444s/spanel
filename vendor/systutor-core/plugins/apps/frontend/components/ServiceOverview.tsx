import { Button } from "@systutor/shell/ui/button";

import type { ServiceDetail } from "../types";

export function ServiceOverview({ service, canManage, pending, updatedAt, onAction }: {
  service: ServiceDetail;
  canManage: boolean;
  pending: boolean;
  updatedAt: number;
  onAction: (action: "refresh" | "logs" | "start" | "stop" | "restart") => void;
}) {
  const running = service.status.toLowerCase().includes("running") || service.status.toLowerCase() === "up";
  const seconds = Math.max(0, Math.round((Date.now() - updatedAt) / 1000));
  const lastRevision = seconds < 60 ? `hace ${seconds} segundos` : `hace ${Math.round(seconds / 60)} minutos`;
  return <div className="space-y-4">
    <dl className="grid gap-3 text-sm sm:grid-cols-2">
      <div><dt className="text-muted-foreground">Estado</dt><dd>{service.status} · {service.health}</dd></div>
      <div><dt className="text-muted-foreground">Imagen</dt><dd>{service.image ?? "—"}</dd></div>
      <div><dt className="text-muted-foreground">Creado</dt><dd>{service.created_at ? new Date(service.created_at).toLocaleString() : "—"}</dd></div>
      <div><dt className="text-muted-foreground">Reinicios</dt><dd>{service.restart_count}</dd></div>
      <div><dt className="text-muted-foreground">Puertos internos</dt><dd>{service.ports.map((port) => `${port}/tcp`).join(", ") || "—"}</dd></div>
      <div><dt className="text-muted-foreground">Última revisión</dt><dd>{lastRevision}</dd></div>
    </dl>
    {canManage ? <div className="flex flex-wrap gap-2">
      <Button size="sm" variant="secondary" disabled={pending} onClick={() => onAction("refresh")}>Actualizar</Button>
      <Button size="sm" variant="secondary" disabled={pending} onClick={() => onAction("logs")}>Solicitar logs</Button>
      <Button size="sm" variant="secondary" disabled={pending} onClick={() => onAction("restart")}>Reiniciar</Button>
      <Button size="sm" variant="secondary" disabled={pending} onClick={() => onAction(running ? "stop" : "start")}>{running ? "Detener" : "Iniciar"}</Button>
    </div> : null}
  </div>;
}
