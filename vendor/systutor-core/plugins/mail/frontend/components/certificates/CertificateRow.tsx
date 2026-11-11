import { cn } from "@systutor/shell/ui/cn";

import { useT } from "@/i18n/use-i18n";
import type { CertificateHealthItem, MailCertificate } from "../../api";
import { deploymentLabel, expiryLabel, sourceLabel, statusLabel, statusTone } from "./format";
import type { PendingJob } from "./types";

export function CertificateRow({
  cert,
  pending,
  highlighted,
  health,
  critical,
  onOpen,
}: {
  cert: MailCertificate;
  pending: PendingJob | undefined;
  highlighted: boolean;
  health: CertificateHealthItem | undefined;
  critical: boolean;
  onOpen: (cert: MailCertificate) => void;
}) {
  const t = useT();
  const label = pending ? t("Pending") : statusLabel(cert.status);
  const healthOk = health?.ok;

  return (
    <div
      role="button"
      tabIndex={0}
      title={t("Double click to manage this certificate")}
      aria-label={t("{hostname}. Double click to manage", { hostname: cert.hostname })}
      onDoubleClick={() => onOpen(cert)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(cert);
        }
      }}
      className={cn(
        "grid cursor-pointer gap-2 rounded-md border border-border px-3 py-3 transition hover:border-primary/60 hover:bg-accent/40 sm:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_auto] sm:items-center",
        highlighted && "border-primary/60 bg-accent/40",
        critical && "border-destructive/60"
      )}
    >
      <div className="min-w-0">
        <p className="break-all font-mono text-sm font-medium text-foreground">
          {cert.hostname}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">{sourceLabel(cert)}</p>
      </div>
      <div className="text-xs text-muted-foreground">
        <p>
          {deploymentLabel(cert)} · {expiryLabel(cert)}
        </p>
        <p className="mt-0.5">
          {t("Health:")} {healthOk === true ? t("OK") : healthOk === false ? t("Failing") : t("No data")}
        </p>
      </div>
      <div className="flex items-center justify-start gap-2 sm:justify-end">
        <span className={cn("text-sm font-medium", pending ? "text-sky-600" : statusTone(cert.status))}>
          {label}
        </span>
        {critical ? (
          <span
            className="h-2 w-2 rounded-full bg-destructive"
            title={t("Has critical alerts")}
            aria-label={t("Has critical alerts")}
          />
        ) : null}
      </div>
    </div>
  );
}
