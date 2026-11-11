import { useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";

import { useT } from "@/i18n/use-i18n";
import type { MailCertificate } from "../api";
import { CertificateDetailDialog } from "./certificates/CertificateDetailDialog";
import { CertificateRow } from "./certificates/CertificateRow";
import { ForceIssueDialog } from "./certificates/ForceIssueDialog";
import { NewCertificateDialog } from "./certificates/NewCertificateDialog";
import { agentStateLabel, timestampLabel } from "./certificates/format";
import { useCertificateManager } from "./certificates/useCertificateManager";

type CertificatesDialogProps = {
  open: boolean;
  canManage: boolean;
  highlightHostname?: string | null;
  /** Dominios visibles en la página; filtra certificados de otros tenants. */
  visibleDomains?: string[];
  /** Si se abre desde una fila, muestra solo el certificado de ese dominio. */
  onlyHostname?: string | null;
  onClose: () => void;
};

export function CertificatesDialog({
  open,
  canManage,
  highlightHostname,
  visibleDomains,
  onlyHostname,
  onClose,
}: CertificatesDialogProps) {
  const t = useT();
  const manager = useCertificateManager(open);
  const [selected, setSelected] = useState<string | null>(null);

  const data = manager.data;
  const allowedDomains = new Set(
    (visibleDomains ?? []).map((domain) => domain.trim().toLowerCase()).filter(Boolean)
  );
  const items = (data?.items ?? []).filter((cert) => {
    if (onlyHostname) return cert.hostname === onlyHostname;
    if (allowedDomains.size === 0) return true;
    if (cert.scope === "global") return false;
    return Boolean(cert.domain && allowedDomains.has(cert.domain.trim().toLowerCase()));
  });
  const selectedCert = selected
    ? items.find((item) => item.hostname === selected) ?? null
    : null;
  const visibleHostnames = new Set(items.map((item) => item.hostname));
  const visibleCritical = [...manager.alertsByHost.entries()].filter(
    ([hostname, alerts]) =>
      visibleHostnames.has(hostname) &&
      alerts.some((alert) => alert.severity === "critical")
  ).length;
  const visibleWarning = [...manager.alertsByHost.entries()].filter(
    ([hostname, alerts]) =>
      visibleHostnames.has(hostname) &&
      alerts.some((alert) => alert.severity === "warning")
  ).length;
  // El estado global del agente incluye certificados de otros tenants; en la
  // vista de Dominios se deriva de los certificados visibles.
  const agentState = manager.health
    ? items.some((item) => manager.healthByHost.get(item.hostname)?.ok === false)
      ? "degraded"
      : "ok"
    : undefined;

  function closeAll() {
    setSelected(null);
    manager.setForceTarget(null);
    manager.setNewOpen(false);
    onClose();
  }

  function openDetail(cert: MailCertificate) {
    setSelected(cert.hostname);
  }

  return (
    <>
      <Dialog
        open={open}
        title={t("TLS certificates")}
        description={t("Spanel · Mail infrastructure")}
        onClose={closeAll}
        zIndexClassName="z-[1150]"
        maxWidthClassName="max-w-3xl"
        actions={
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">
              {data?.generated_at
                ? t("Last read: {time}", {
                    time: new Date(data.generated_at).toLocaleString(),
                  })
                : t("No agent reading")}
            </p>
            <div className="flex gap-2">
              {canManage ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={manager.busy}
                  onClick={() => {
                    manager.setActionError(null);
                    manager.setNewOpen(true);
                  }}
                >
                  {t("Add certificate")}
                </Button>
              ) : null}
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={!canManage || manager.busy || manager.refreshMutation.isPending}
                onClick={() => {
                  manager.setActionError(null);
                  manager.refreshMutation.mutate();
                }}
              >
                {manager.refreshMutation.isPending ? t("Requesting...") : t("Refresh status")}
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={closeAll}>
                {t("Close")}
              </Button>
            </div>
          </div>
        }
      >
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-xs text-muted-foreground">
            <span>
              {t("Agent status:")} <strong>{agentStateLabel(agentState)}</strong>
            </span>
            <span>
              {t("Last check:")}{" "}
              {timestampLabel(manager.health?.agent_last_run_at ?? manager.health?.generated_at)}
            </span>
            <span>
              {manager.health
                ? t("{critical} critical · {warning} warnings", {
                    critical: visibleCritical,
                    warning: visibleWarning,
                  })
                : t("No published health")}
            </span>
          </div>

          {manager.unavailableMessage ? (
            <Alert title={t("Status unavailable")}>
              {manager.unavailableMessage}.{" "}
              {t(
                "Issuing actions remain disabled until the agent publishes recent status."
              )}
            </Alert>
          ) : null}
          {manager.actionError ? (
            <Alert title={t("Could not complete the action")}>{manager.actionError}</Alert>
          ) : null}
          {manager.globalCritical ? (
            <Alert title={t("Global critical alert")}>
              {t(
                "The default certificate or the agent has a critical alert. Destructive actions are disabled."
              )}
            </Alert>
          ) : null}

          {manager.listLoading ? (
            <p className="text-sm text-muted-foreground">{t("Reading certificates...")}</p>
          ) : null}

          {!manager.listLoading && items.length === 0 ? (
            <p className="rounded-md border border-border px-3 py-4 text-sm text-muted-foreground">
              {onlyHostname
                ? t("No certificate for {hostname}.", { hostname: onlyHostname })
                : t("No certificates visible for this tenant.")}
            </p>
          ) : null}

          <div className="space-y-2">
            {items.map((cert) => (
              <CertificateRow
                key={cert.hostname}
                cert={cert}
                pending={manager.pending[cert.hostname]}
                highlighted={
                  Boolean(highlightHostname) && cert.hostname === highlightHostname
                }
                health={manager.healthByHost.get(cert.hostname)}
                critical={manager.criticalHosts.has(cert.hostname)}
                onOpen={openDetail}
              />
            ))}
          </div>
        </div>
      </Dialog>

      {selectedCert ? (
        <CertificateDetailDialog
          cert={selectedCert}
          health={manager.healthByHost.get(selectedCert.hostname)}
          alerts={manager.alertsByHost.get(selectedCert.hostname) ?? []}
          pending={manager.pending[selectedCert.hostname]}
          canManage={canManage}
          actionsDisabled={manager.stateUnavailable || manager.busy}
          destructiveDisabled={
            manager.destructiveDisabled || manager.criticalHosts.has(selectedCert.hostname)
          }
          onClose={() => setSelected(null)}
          onRegenerate={manager.regenerate}
          onForce={manager.setForceTarget}
          onAction={manager.runAction}
        />
      ) : null}

      <ForceIssueDialog
        target={manager.forceTarget}
        pending={manager.requestMutation.isPending}
        disabled={manager.destructiveDisabled}
        onCancel={() => manager.setForceTarget(null)}
        onConfirm={manager.confirmForce}
      />

      <NewCertificateDialog
        open={manager.newOpen}
        pending={manager.requestMutation.isPending}
        error={manager.actionError}
        onSubmit={manager.requestNew}
        onClose={() => manager.setNewOpen(false)}
      />
    </>
  );
}
