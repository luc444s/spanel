import type { KeyboardEvent } from "react";

import { useT } from "@/i18n/use-i18n";

import type { ServiceDetail } from "../types";
import { StatusIndicator } from "./StatusIndicator";

export function ServiceCard({ service, domainCount, onOpen }: {
  service: ServiceDetail;
  domainCount: number | null;
  onOpen: () => void;
}) {
  const t = useT();
  function keyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onOpen();
  }
  return <article role="button" tabIndex={0} aria-label={t("Open service {name}", { name: service.name })}
    title={t("Double-click to manage the service")}
    className="cursor-pointer rounded border border-border p-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
    onDoubleClick={onOpen} onKeyDown={keyDown}>
    <div className="flex items-center justify-between gap-2">
      <strong>{service.name}</strong><StatusIndicator status={service.status} className="text-xs text-muted-foreground" />
    </div>
    <p className="mt-1 truncate text-xs text-muted-foreground" title={service.image ?? undefined}>{service.image ?? t("Image not registered")}</p>
    <p className="mt-2">{service.health === "unknown" ? t("Health unknown") : service.health}</p>
    <p className="text-xs text-muted-foreground">{t("Ports: {ports}", { ports: service.ports.join(", ") || "—" })}</p>
    <p className="text-xs text-muted-foreground">{domainCount === null ? t("Domains: —") : domainCount ? (domainCount === 1 ? t("{count} domain", { count: domainCount }) : t("{count} domains", { count: domainCount })) : t("No domains")}</p>
  </article>;
}
