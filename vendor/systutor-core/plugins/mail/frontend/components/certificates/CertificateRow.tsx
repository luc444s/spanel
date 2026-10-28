import { cn } from "@systutor/shell/ui/cn";

import type { CertificateHealthItem, MailCertificate } from "../../api";
import { STATUS_LABELS, deploymentLabel, expiryLabel, sourceLabel, statusTone } from "./format";
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
  const label = pending ? "Pendiente" : STATUS_LABELS[cert.status];
  const healthOk = health?.ok;

  return (
    <div
      role="button"
      tabIndex={0}
      title="Doble clic para administrar este certificado"
      aria-label={`${cert.hostname}. Doble clic para administrar`}
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
          Salud: {healthOk === true ? "OK" : healthOk === false ? "Fallo" : "Sin datos"}
        </p>
      </div>
      <div className="flex items-center justify-start gap-2 sm:justify-end">
        <span className={cn("text-sm font-medium", pending ? "text-sky-600" : statusTone(cert.status))}>
          {label}
        </span>
        {critical ? (
          <span
            className="h-2 w-2 rounded-full bg-destructive"
            title="Tiene alertas críticas"
            aria-label="Tiene alertas críticas"
          />
        ) : null}
      </div>
    </div>
  );
}
