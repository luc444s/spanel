import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { toast } from "@systutor/shell/ui/toast";

import {
  certificateHealthKeys,
  certificateKeys,
  getMailCertificateAlerts,
  getMailCertificateHealth,
  getMailCertificateJob,
  listMailCertificates,
  refreshMailCertificates,
  requestMailCertificate,
  runMailCertificateAction,
  type CertificateAdminAction,
  type CertificateAlert,
  type MailCertificate,
} from "../../api";
import type { PendingJob } from "./types";

/** Estado, consultas y mutaciones del gestor de certificados. */
export function useCertificateManager(open: boolean) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<Record<string, PendingJob>>({});
  const [forceTarget, setForceTarget] = useState<MailCertificate | null>(null);
  const [newOpen, setNewOpen] = useState(false);
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

  const healthQuery = useQuery({
    queryKey: certificateHealthKeys.health,
    queryFn: getMailCertificateHealth,
    enabled: open,
    retry: false,
    refetchInterval: open ? 30_000 : false,
  });

  const alertsQuery = useQuery({
    queryKey: certificateHealthKeys.alerts,
    queryFn: getMailCertificateAlerts,
    enabled: open,
    retry: false,
    refetchInterval: open ? 30_000 : false,
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
      void queryClient.invalidateQueries({ queryKey: certificateHealthKeys.health });
    },
    onError: (error: Error) => setActionError(error.message || "No se pudo solicitar el refresco"),
  });

  const adminMutation = useMutation({
    mutationFn: ({
      hostname,
      action,
    }: {
      hostname: string;
      action: CertificateAdminAction;
    }) => runMailCertificateAction(hostname, action),
    onSuccess: (data, variables) => {
      setActionError(null);
      setPending((current) => ({
        ...current,
        [data.hostname]: { jobId: data.job_id, state: "queued" },
      }));
      toast.success(`Acción ${variables.action} encolada para ${data.hostname}`);
      void queryClient.invalidateQueries({ queryKey: certificateHealthKeys.health });
    },
    onError: (error: Error) => {
      setActionError(error.message || "No se pudo ejecutar la acción administrativa");
    },
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
  const busy =
    requestMutation.isPending || refreshMutation.isPending || adminMutation.isPending;
  const health = healthQuery.data;
  const healthByHost = new Map(
    (health?.certificates ?? []).map((item) => [item.hostname, item])
  );
  // El certificado predeterminado no está en la lista de salud: se proyecta
  // desde `health.default` para que la fila de cf-mail no diga "Sin datos".
  if (health?.default) {
    healthByHost.set(health.default.name, {
      hostname: health.default.name,
      serial: health.default.serial,
      served_serial: health.default.no_sni_serial,
      no_sni_serial: health.default.no_sni_serial,
      not_after: null,
      days_left: null,
      sni: {},
      no_sni: health.default.ok,
      no_sni_ports: {},
      ok: health.default.ok,
      last_verified_at: health.generated_at,
      last_ok_at: null,
      consecutive_failures: 0,
      last_error: null,
    });
  }
  const alerts: CertificateAlert[] = alertsQuery.data?.alerts ?? [];
  const globalCritical = alerts.some(
    (alert) => alert.severity === "critical" && !alert.hostname
  );
  const alertsByHost = new Map<string, CertificateAlert[]>();
  for (const alert of alerts) {
    if (!alert.hostname) continue;
    const current = alertsByHost.get(alert.hostname) ?? [];
    current.push(alert);
    alertsByHost.set(alert.hostname, current);
  }
  const criticalHosts = new Set(
    alerts
      .filter((alert) => alert.severity === "critical" && alert.hostname)
      .map((alert) => alert.hostname as string)
  );
  const destructiveDisabled = stateUnavailable || busy || globalCritical;

  function regenerate(cert: MailCertificate) {
    setActionError(null);
    requestMutation.mutate({ hostname: cert.hostname, action: "ensure" });
  }

  function confirmForce() {
    if (!forceTarget) return;
    requestMutation.mutate({ hostname: forceTarget.hostname, action: "force" });
  }

  /**
   * Alta de un hostname `mail.<dominio>` (A.SPEC 0041): reutiliza la misma
   * mutación y el polling de jobs; el diálogo se cierra solo al encolar.
   */
  function requestNew(hostname: string) {
    setActionError(null);
    requestMutation.mutate(
      { hostname, action: "ensure" },
      { onSuccess: () => setNewOpen(false) }
    );
  }

  function runAction(cert: MailCertificate, action: CertificateAdminAction) {
    setActionError(null);
    adminMutation.mutate({ hostname: cert.hostname, action });
  }

  return {
    data,
    listLoading: listQuery.isLoading,
    stateUnavailable,
    unavailableMessage,
    actionError,
    setActionError,
    busy,
    health,
    healthByHost,
    alertsByHost,
    criticalHosts,
    globalCritical,
    destructiveDisabled,
    pending,
    forceTarget,
    setForceTarget,
    newOpen,
    setNewOpen,
    requestMutation,
    refreshMutation,
    regenerate,
    confirmForce,
    requestNew,
    runAction,
  };
}
