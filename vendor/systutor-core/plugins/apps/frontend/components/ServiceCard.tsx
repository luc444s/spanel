import type { KeyboardEvent } from "react";

import { Button } from "@systutor/shell/ui/button";

import type { ServiceDetail } from "../types";

export function ServiceCard({ service, domainCount, onOpen }: {
  service: ServiceDetail;
  domainCount: number | null;
  onOpen: () => void;
}) {
  function keyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onOpen();
  }
  return <article role="button" tabIndex={0} aria-label={`Abrir servicio ${service.name}`}
    className="rounded border border-border p-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
    onDoubleClick={onOpen} onKeyDown={keyDown}>
    <div className="flex items-center justify-between gap-2">
      <strong>{service.name}</strong><span className="text-xs text-muted-foreground">● {service.status}</span>
    </div>
    <p className="mt-1 truncate text-xs text-muted-foreground" title={service.image ?? undefined}>{service.image ?? "Imagen no registrada"}</p>
    <p className="mt-2">{service.health === "unknown" ? "Health desconocido" : service.health}</p>
    <p className="text-xs text-muted-foreground">Puertos: {service.ports.join(", ") || "—"}</p>
    <p className="text-xs text-muted-foreground">{domainCount === null ? "Dominios: —" : domainCount ? `${domainCount} dominio${domainCount === 1 ? "" : "s"}` : "Sin dominios"}</p>
    <Button className="mt-3" variant="secondary" onClick={(event) => { event.stopPropagation(); onOpen(); }}>Abrir servicio</Button>
  </article>;
}
