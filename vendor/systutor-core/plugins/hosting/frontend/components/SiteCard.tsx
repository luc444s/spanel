import { Badge } from "@systutor/shell/ui/badge";
import { Card } from "@systutor/shell/ui/card";
import { cn } from "@systutor/shell/ui/cn";

import { t } from "@/i18n";
import { useT } from "@/i18n/use-i18n";

import type { HostingSite } from "../api";

const STATUS_LABELS: Record<string, string> = {
  running: "Running",
  exited: "Stopped",
  created: "Created",
  paused: "Paused",
  missing: "Missing",
};

const KIND_LABELS: Record<HostingSite["kind"], string> = {
  wordpress: "WordPress",
  static: "Static",
};

export function stateLabel(status: string) {
  return STATUS_LABELS[status] ? t(STATUS_LABELS[status]) : status;
}

export function kindLabel(kind: HostingSite["kind"]) {
  return KIND_LABELS[kind] ? t(KIND_LABELS[kind]) : kind;
}

function statusDot(status: string) {
  if (status === "running") return "bg-emerald-500";
  if (status === "missing") return "bg-red-500";
  if (status === "exited" || status === "created") return "bg-zinc-400";
  return "bg-amber-500";
}

type SiteCardProps = {
  site: HostingSite;
  readOnly?: boolean;
  onOpen: (site: HostingSite) => void;
};

/**
 * One site is one card. The card carries no action buttons: clicking it opens
 * the dialog that owns every action, so the list stays readable on a phone.
 */
export function SiteCard({ site, readOnly = false, onOpen }: SiteCardProps) {
  const t = useT();

  return (
    <Card
      role="button"
      tabIndex={0}
      onClick={() => onOpen(site)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(site);
        }
      }}
      className="cursor-pointer p-4 transition hover:border-primary/60 hover:bg-accent/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
    >
      <div className="flex items-start justify-between gap-3">
        <p className="break-all font-mono text-sm font-medium text-card-foreground">
          {site.container_name}
        </p>
        <Badge className="shrink-0">
          <span className={cn("mr-1.5 h-2 w-2 rounded-full", statusDot(site.status))} />
          {stateLabel(site.status)}
        </Badge>
      </div>
      <p className="mt-1 truncate text-sm text-muted-foreground">
        {site.domain ?? t("No domain")}
      </p>
      {site.tenant_name ? (
        <p className="mt-1 truncate text-xs text-muted-foreground">
          {t("Tenant")}: {site.tenant_name}
        </p>
      ) : null}
      <div className="flex items-center gap-2">
        <Badge>{kindLabel(site.kind)}</Badge>
        {/* A.SPEC 0042: a static site carries its profile as a badge */}
        {site.kind === "static" && site.profile ? (
          <Badge className="border border-border text-muted-foreground">{site.profile}</Badge>
        ) : null}
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        {!readOnly && site.managed ? t("Managed") : t("Read only")}
      </p>
    </Card>
  );
}
