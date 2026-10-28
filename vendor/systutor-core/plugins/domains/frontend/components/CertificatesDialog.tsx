import { useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";

import type { MailCertificate } from "../api";
import { CertificateDetailDialog } from "./certificates/CertificateDetailDialog";
import { CertificateRow } from "./certificates/CertificateRow";
import { ForceIssueDialog } from "./certificates/ForceIssueDialog";
import { agentStateLabel, timestampLabel } from "./certificates/format";
import { useCertificateManager } from "./certificates/useCertificateManager";

type CertificatesDialogProps = {
  open: boolean;
  canManage: boolean;
  highlightHostname?: string | null;
  onClose: () => void;
};

export function CertificatesDialog({
  open,
  canManage,
  highlightHostname,
  onClose,
}: CertificatesDialogProps) {
  const manager = useCertificateManager(open);
  const [selected, setSelected] = useState<string | null>(null);

  const data = manager.data;
  const selectedCert = selected
    ? data?.items.find((item) => item.hostname === selected) ?? null
    : null;

  function closeAll() {
    setSelected(null);
    manager.setForceTarget(null);
    onClose();
  }

  function openDetail(cert: MailCertificate) {
    setSelected(cert.hostname);
  }

  return (
    <>
      <Dialog
        open={open}
        title="Certificados TLS"
        description="Spanel · Infraestructura de correo"
        onClose={closeAll}
        zIndexClassName="z-[1150]"
        maxWidthClassName="max-w-3xl"
        actions={
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">
              {data?.generated_at
                ? `Última lectura: ${new Date(data.generated_at).toLocaleString()}`
                : "Sin lectura del agente"}
            </p>
            <div className="flex gap-2">
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
                {manager.refreshMutation.isPending ? "Solicitando..." : "Actualizar estado"}
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={closeAll}>
                Cerrar
              </Button>
            </div>
          </div>
        }
      >
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-xs text-muted-foreground">
            <span>
              Estado del agente: <strong>{agentStateLabel(manager.health?.agent_state)}</strong>
            </span>
            <span>
              Última verificación:{" "}
              {timestampLabel(manager.health?.agent_last_run_at ?? manager.health?.generated_at)}
            </span>
            <span>
              {manager.health
                ? `${manager.health.critical_count} críticas · ${manager.health.warning_count} avisos`
                : "Sin salud publicada"}
            </span>
          </div>

          {manager.unavailableMessage ? (
            <Alert title="Estado no disponible">
              {manager.unavailableMessage}. Las acciones de emisión quedan deshabilitadas
              hasta que el agente publique estado reciente.
            </Alert>
          ) : null}
          {manager.actionError ? (
            <Alert title="No se pudo completar la acción">{manager.actionError}</Alert>
          ) : null}
          {manager.globalCritical ? (
            <Alert title="Alerta crítica global">
              El certificado predeterminado o el agente tienen una alerta crítica. Las
              acciones destructivas están deshabilitadas.
            </Alert>
          ) : null}

          {manager.listLoading ? (
            <p className="text-sm text-muted-foreground">Leyendo certificados...</p>
          ) : null}

          {!manager.listLoading && (data?.items.length ?? 0) === 0 ? (
            <p className="rounded-md border border-border px-3 py-4 text-sm text-muted-foreground">
              No hay certificados visibles para este tenant.
            </p>
          ) : null}

          <div className="space-y-2">
            {data?.items.map((cert) => (
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

          <p className="text-xs text-muted-foreground">
            Doble clic en un certificado para ver alertas y administrarlo. La emisión la
            ejecuta el agente privilegiado del host; la web nunca recibe claves privadas.
          </p>
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
    </>
  );
}
