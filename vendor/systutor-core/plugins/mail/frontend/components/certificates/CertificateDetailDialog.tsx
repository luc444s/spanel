import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { cn } from "@systutor/shell/ui/cn";

import { useT } from "@/i18n/use-i18n";
import type {
  CertificateAdminAction,
  CertificateAlert,
  CertificateHealthItem,
  MailCertificate,
} from "../../api";
import {
  deploymentLabel,
  expiryLabel,
  sourceLabel,
  statusLabel,
  statusTone,
  timestampLabel,
} from "./format";
import type { PendingJob } from "./types";

export function CertificateDetailDialog({
  cert,
  health,
  alerts,
  pending,
  canManage,
  actionsDisabled,
  destructiveDisabled,
  onClose,
  onRegenerate,
  onForce,
  onAction,
}: {
  cert: MailCertificate;
  health: CertificateHealthItem | undefined;
  alerts: CertificateAlert[];
  pending: PendingJob | undefined;
  canManage: boolean;
  actionsDisabled: boolean;
  destructiveDisabled: boolean;
  onClose: () => void;
  onRegenerate: (cert: MailCertificate) => void;
  onForce: (cert: MailCertificate) => void;
  onAction: (cert: MailCertificate, action: CertificateAdminAction) => void;
}) {
  const t = useT();
  const global = cert.scope === "global" || cert.deployment === "default";
  const busy = Boolean(pending) || actionsDisabled;
  const destructive = busy || destructiveDisabled;
  const critical = alerts.some((alert) => alert.severity === "critical");
  const label = pending ? t("Pending") : statusLabel(cert.status);

  return (
    <Dialog
      open
      title={cert.hostname}
      description={sourceLabel(cert)}
      onClose={onClose}
      zIndexClassName="z-[1200]"
      maxWidthClassName="max-w-xl"
      actions={
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className={cn("text-sm font-medium", pending ? "text-sky-600" : statusTone(cert.status))}>
            {label}
          </span>
          <Button type="button" variant="secondary" size="sm" onClick={onClose}>
            {t("Close")}
          </Button>
        </div>
      }
    >
      <div className="space-y-4 text-sm">
        {alerts.length > 0 ? (
          <Alert title={critical ? t("Critical alert") : t("Warning")}>
            <ul className="list-disc space-y-1 pl-4">
              {alerts.map((alert) => (
                <li key={`${alert.code}:${alert.created_at ?? ""}`}>{alert.message}</li>
              ))}
            </ul>
          </Alert>
        ) : (
          <Alert title={t("No alerts")}>{t("The certificate has no active alerts.")}</Alert>
        )}

        {pending ? (
          <p className="text-xs text-sky-600">
            {t("A job is in progress; the result appears when it finishes.")}
          </p>
        ) : null}
        {health?.last_error ? (
          <p className="break-words text-xs text-destructive">{health.last_error}</p>
        ) : null}

        <dl className="grid gap-2 sm:grid-cols-2">
          <div>
            <dt className="text-xs text-muted-foreground">{t("Deployment")}</dt>
            <dd>{deploymentLabel(cert)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">{t("Expiry")}</dt>
            <dd>{expiryLabel(cert)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">{t("Health")}</dt>
            <dd>
              {health?.ok === true ? t("OK") : health?.ok === false ? t("With failures") : t("No data")}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">{t("Last check")}</dt>
            <dd>{timestampLabel(health?.last_verified_at ?? cert.last_checked_at)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">{t("Last OK")}</dt>
            <dd>{timestampLabel(health?.last_ok_at)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">{t("Consecutive failures")}</dt>
            <dd>{health?.consecutive_failures ?? 0}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">{t("Stored serial")}</dt>
            <dd className="break-all font-mono text-xs">{cert.serial ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">{t("Served serial")}</dt>
            <dd className="break-all font-mono text-xs">{health?.served_serial ?? "—"}</dd>
          </div>
        </dl>

        {health && Object.keys(health.sni).length > 0 ? (
          <div>
            <p className="text-xs text-muted-foreground">{t("SNI probes")}</p>
            <p className="mt-1 flex flex-wrap gap-2 text-xs">
              {Object.entries(health.sni).map(([port, ok]) => (
                <span
                  key={port}
                  className={cn(
                    "rounded border px-2 py-0.5",
                    ok
                      ? "border-emerald-200 text-emerald-700"
                      : "border-destructive/40 text-destructive"
                  )}
                >
                  {ok
                    ? t("{port}: ok", { port })
                    : t("{port}: fails", { port })}
                </span>
              ))}
              {health.no_sni === false ? (
                <span className="rounded border border-destructive/40 px-2 py-0.5 text-destructive">
                  {t("No SNI: fails")}
                </span>
              ) : null}
            </p>
          </div>
        ) : null}

        <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-3">
          {canManage ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={busy}
              onClick={() => onAction(cert, "verify")}
              title={t("Force read-only verification")}
            >
              {t("Verify")}
            </Button>
          ) : null}
          {canManage && !global ? (
            <>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={destructive || cert.status === "missing"}
                onClick={() => onForce(cert)}
                title={t("Force reissue with Certbot")}
              >
                {t("Force")}
              </Button>
              <Button type="button" size="sm" disabled={busy} onClick={() => onRegenerate(cert)}>
                {t("Regenerate")}
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={destructive}
                onClick={() => onAction(cert, "sync")}
                title={t("Install the Certbot-renewed lineage if it changed")}
              >
                {t("Sync")}
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={destructive}
                onClick={() => onAction(cert, "rollback")}
                title={t("Revert to the previous generation")}
              >
                {t("Roll back")}
              </Button>
            </>
          ) : null}
          {global ? (
            <span className="self-center text-xs text-muted-foreground">
              {t("Global default")}
            </span>
          ) : null}
        </div>
      </div>
    </Dialog>
  );
}
