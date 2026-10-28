import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Badge } from "@systutor/shell/ui/badge";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { toast } from "@systutor/shell/ui/toast";
import { cn } from "@systutor/shell/ui/cn";

import {
  certificateKeys,
  getMailCertificateJob,
  listMailCertificates,
  refreshMailCertificates,
  requestMailCertificate,
  type MailCertificate,
} from "../api";

type CertificatesDialogProps = {
  open: boolean;
  canManage: boolean;
  highlightHostname?: string | null;
  onClose: () => void;
};

type PendingJob = { jobId: string; state: "queued" | "running" };

const STATUS_LABELS: Record<MailCertificate["status"], string> = {
  active: "Válido",
  expiring: "Por vencer",
  expired: "Vencido",
  missing: "Sin certificado",
  error: "Error",
};

function statusTone(status: MailCertificate["status"]) {
  if (status === "active") return "text-emerald-600";
  if (status === "expiring") return "text-amber-600";
  if (status === "expired" || status === "error") return "text-destructive";
  return "text-muted-foreground";
}

function sourceLabel(cert: MailCertificate) {
  if (cert.scope === "global") return "Global · Predeterminado";
  const source =
    cert.source === "certbot" ? "Certbot" : cert.source === "step-ca" ? "step-ca" : "Estático";
  if (cert.deployment === "sni") return `${source} · SNI`;
  if (cert.deployment === "none") return `${source} · Sin desplegar`;
  if (cert.deployment === "failed") return `${source} · Falló`;
  return `${source} · Predeterminado`;
}

function deploymentLabel(cert: MailCertificate) {
  if (cert.deployment === "sni") return "SNI";
  if (cert.deployment === "default") return "Predeterminado";
  if (cert.deployment === "none") return "Sin desplegar";
  return "Falló";
}

function expiryLabel(cert: MailCertificate) {
  if (!cert.not_after) return "Sin fecha";
  const target = new Date(cert.not_after).getTime();
  if (Number.isNaN(target)) return "Sin fecha";
  const days = Math.ceil((target - Date.now()) / 86_400_000);
  if (days < 0) return `Venció hace ${Math.abs(days)} ${Math.abs(days) === 1 ? "día" : "días"}`;
  if (days === 0) return "Vence hoy";
  return `Vence en ${days} ${days === 1 ? "día" : "días"}`;
}

function CertificateRow({
  cert,
  pending,
  canManage,
  actionsDisabled,
  highlighted,
  onRegenerate,
  onForce,
}: {
  cert: MailCertificate;
  pending: PendingJob | undefined;
  canManage: boolean;
  actionsDisabled: boolean;
  highlighted: boolean;
  onRegenerate: (cert: MailCertificate) => void;
  onForce: (cert: MailCertificate) => void;
}) {
  const global = cert.scope === "global" || cert.deployment === "default";
  const busy = Boolean(pending) || actionsDisabled;
  const label = pending ? "Pendiente" : STATUS_LABELS[cert.status];

  return (
    <div
      className={cn(
        "grid gap-2 rounded-md border border-border px-3 py-3 sm:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_auto_auto] sm:items-center",
        highlighted && "border-primary/60 bg-accent/40"
      )}
    >
      <div className="min-w-0">
        <p className="break-all font-mono text-sm font-medium text-foreground">
          {cert.hostname}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">{sourceLabel(cert)}</p>
      </div>
      <div className="text-xs text-muted-foreground">
        <p>{deploymentLabel(cert)}</p>
        <p className="mt-0.5">{expiryLabel(cert)}</p>
      </div>
      <div className="flex items-center gap-2">
        <span className={cn("text-sm font-medium", pending ? "text-sky-600" : statusTone(cert.status))}>
          {label}
        </span>
        {pending ? <Badge>Job activo</Badge> : null}
      </div>
      <div className="flex justify-end gap-2">
        {canManage && !global ? (
          <>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={busy || cert.status === "missing"}
              onClick={() => onForce(cert)}
              title="Forzar reemisión con Certbot"
            >
              Forzar
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={busy}
              onClick={() => onRegenerate(cert)}
            >
              Regenerar
            </Button>
          </>
        ) : null}
        {canManage && global ? (
          <span className="text-xs text-muted-foreground">Predeterminado global</span>
        ) : null}
      </div>
    </div>
  );
}

export function CertificatesDialog({
  open,
  canManage,
  highlightHostname,
  onClose,
}: CertificatesDialogProps) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<Record<string, PendingJob>>({});
  const [forceTarget, setForceTarget] = useState<MailCertificate | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const pendingRef = useRef(pending);
  pendingRef.current = pending;

  const listQuery = useQuery({
    queryKey: certificateKeys.list,
    queryFn: listMailCertificates,
    enabled: open,
    retry: false,
    refetchInterval: open && Object.keys(pending).length > 0 ? 5_000 : false,
  });

  const requestMutation = useMutation({
    mutationFn: ({
      hostname,
      action,
    }: {
      hostname: string;
      action: "ensure" | "force";
    }) => requestMailCertificate(hostname, action),
    onSuccess: (data) => {
      setActionError(null);
      setForceTarget(null);
      setPending((current) => ({
        ...current,
        [data.hostname]: { jobId: data.job_id, state: "queued" },
      }));
      toast.success(`Solicitud encolada para ${data.hostname}`);
      void queryClient.invalidateQueries({ queryKey: certificateKeys.list });
    },
    onError: (error: Error) => {
      setForceTarget(null);
      setActionError(error.message || "No se pudo encolar la solicitud");
    },
  });

  const refreshMutation = useMutation({
    mutationFn: refreshMailCertificates,
    onSuccess: () => {
      toast.success("Refresco de estado solicitado al agente");
      void queryClient.invalidateQueries({ queryKey: certificateKeys.list });
    },
    onError: (error: Error) => setActionError(error.message || "No se pudo solicitar el refresco"),
  });

  // Un job se consulta hasta que termina. Un fallo se muestra una vez y no se
  // reintenta en bucle: el botón vuelve a habilitarse.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      const entries = Object.entries(pendingRef.current);
      for (const [hostname, entry] of entries) {
        getMailCertificateJob(hostname, entry.jobId)
          .then((job) => {
            if (cancelled) return;
            if (job.state === "succeeded") {
              setPending((current) => {
                const next = { ...current };
                delete next[hostname];
                return next;
              });
              toast.success(`Certificado actualizado: ${hostname}`);
              void queryClient.invalidateQueries({ queryKey: certificateKeys.list });
            } else if (job.state === "failed") {
              setPending((current) => {
                const next = { ...current };
                delete next[hostname];
                return next;
              });
              setActionError(job.error || `El agente no pudo procesar ${hostname}`);
              void queryClient.invalidateQueries({ queryKey: certificateKeys.list });
            } else {
              setPending((current) => {
                const existing = current[hostname];
                if (!existing || existing.state === job.state) return current;
                return { ...current, [hostname]: { ...existing, state: job.state } };
              });
            }
          })
          .catch(() => {
            // Si el job todavía no existe o el agente está caído, se conserva
            // el estado pendiente y se reintenta en el próximo ciclo.
          });
      }
    }, 3_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [open, queryClient]);

  const data = listQuery.data;
  const stateUnavailable =
    Boolean(data?.stale) ||
    Boolean(data?.error) ||
    listQuery.isError ||
    (!listQuery.isLoading && !data);
  const unavailableMessage = data?.error
    ? "Estado no disponible"
    : listQuery.isError
      ? "Estado no disponible"
      : data?.stale
        ? "Estado no disponible: el agente no publicó estado reciente"
        : null;
  const busy = requestMutation.isPending || refreshMutation.isPending;

  function regenerate(cert: MailCertificate) {
    setActionError(null);
    requestMutation.mutate({ hostname: cert.hostname, action: "ensure" });
  }

  function confirmForce() {
    if (!forceTarget) return;
    requestMutation.mutate({ hostname: forceTarget.hostname, action: "force" });
  }

  return (
    <>
      <Dialog
        open={open}
        title="Certificados TLS"
        description="Spanel · Infraestructura de correo"
        onClose={onClose}
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
                disabled={!canManage || busy || refreshMutation.isPending}
                onClick={() => {
                  setActionError(null);
                  refreshMutation.mutate();
                }}
              >
                {refreshMutation.isPending ? "Solicitando..." : "Actualizar estado"}
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={onClose}>
                Cerrar
              </Button>
            </div>
          </div>
        }
      >
        <div className="space-y-3">
          {unavailableMessage ? (
            <Alert title="Estado no disponible">
              {unavailableMessage}. Las acciones de emisión quedan deshabilitadas hasta
              que el agente publique estado reciente.
            </Alert>
          ) : null}
          {actionError ? <Alert title="No se pudo completar la acción">{actionError}</Alert> : null}

          {listQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">Leyendo certificados...</p>
          ) : null}

          {!listQuery.isLoading && (data?.items.length ?? 0) === 0 ? (
            <p className="rounded-md border border-border px-3 py-4 text-sm text-muted-foreground">
              No hay certificados visibles para este tenant.
            </p>
          ) : null}

          <div className="space-y-2">
            {data?.items.map((cert) => (
              <CertificateRow
                key={cert.hostname}
                cert={cert}
                pending={pending[cert.hostname]}
                canManage={canManage}
                actionsDisabled={stateUnavailable || busy}
                highlighted={
                  Boolean(highlightHostname) && cert.hostname === highlightHostname
                }
                onRegenerate={regenerate}
                onForce={setForceTarget}
              />
            ))}
          </div>

          <p className="text-xs text-muted-foreground">
            La emisión la ejecuta el agente privilegiado del host. La web nunca recibe
            claves privadas; solo rutas y metadatos.
          </p>
        </div>
      </Dialog>

      <Dialog
        open={Boolean(forceTarget)}
        title="Forzar reemisión"
        description={forceTarget?.hostname}
        onClose={() => setForceTarget(null)}
        zIndexClassName="z-[1200]"
        maxWidthClassName="max-w-md"
      >
        <div className="space-y-4 text-sm text-foreground">
          <Alert title="Advertencia de rate limit">
            Let’s Encrypt limita los certificados duplicados por dominio. Forzar una
            emisión innecesaria puede bloquear nuevas solicitudes durante una semana.
          </Alert>
          <p>
            Se pedirá a Certbot que reemplace el certificado de{" "}
            <span className="break-all font-mono text-xs">{forceTarget?.hostname}</span>.
            El correo sigue funcionando con el certificado actual hasta que el nuevo se
            valide e instale.
          </p>
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="secondary"
              onClick={() => setForceTarget(null)}
              disabled={requestMutation.isPending}
            >
              Cancelar
            </Button>
            <Button
              type="button"
              onClick={confirmForce}
              disabled={requestMutation.isPending || stateUnavailable}
            >
              {requestMutation.isPending ? "Solicitando..." : "Forzar emisión"}
            </Button>
          </div>
        </div>
      </Dialog>
    </>
  );
}
