import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { toast } from "@systutor/shell/ui/toast";

import {
  appsKeys, getApplication, inspectApplication, listOperations, newIdempotencyKey,
  runApplicationAction,
} from "../api";
import { actionAllowed, hasBlockingFindings } from "../logic";
import type { AppAction, Application, AuditReport, Finding, OperationSummary } from "../types";
import { DeleteDialog } from "./DeleteDialog";
import { DeployDialog } from "./DeployDialog";
import { FindingsReport } from "./FindingsReport";
import { SourceDialog } from "./SourceDialog";

type Tab = "overview" | "audit" | "deployments" | "operations" | "logs";
const ACTIONS: Array<[AppAction, string]> = [
  ["refresh", "Actualizar"], ["logs", "Solicitar logs"], ["start", "Iniciar"],
  ["stop", "Detener"], ["restart", "Reiniciar"], ["down", "Down"],
];

export function ApplicationDetail({ application, canCreate, canManage,
  canDeploy, canDelete, onClose, onAccepted }: {
  application: Application | null;
  canCreate: boolean;
  canManage: boolean;
  canDeploy: boolean;
  canDelete: boolean;
  onClose: () => void;
  onAccepted: (operation: OperationSummary) => void;
}) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");
  const [deployOpen, setDeployOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const detail = useQuery({
    queryKey: appsKeys.detail(application?.id ?? "idle"),
    queryFn: () => getApplication(application!.id),
    enabled: Boolean(application),
    refetchInterval: (state) => {
      const current = state.state.data;
      return current && ["building", "deploying"].includes(current.status) ? 3_000 : false;
    },
  });
  const operationsQuery = useQuery({
    queryKey: appsKeys.operations(application?.id ?? "idle"),
    queryFn: () => listOperations(application!.id),
    enabled: Boolean(application),
    refetchInterval: 3_000,
  });
  const appOperations = useMemo(() => {
    return [...(operationsQuery.data ?? [])].sort((left, right) =>
      String(right.created_at ?? "").localeCompare(String(left.created_at ?? "")));
  }, [operationsQuery.data]);
  const current = detail.data ? { ...detail.data } : application;
  const busy = appOperations.some((item) => !["succeeded", "failed", "interrupted"].includes(item.status));
  const auditOperation = appOperations.find((item) => {
    const candidate = (item.result ?? item.report) as AuditReport | null;
    return item.kind === "inspect" && Array.isArray(candidate?.findings);
  });
  const report = (auditOperation?.result ?? auditOperation?.report ?? {}) as AuditReport;
  const operationFindings = Array.isArray(report.findings) ? report.findings : null;
  const findings = (operationFindings ?? detail.data?.findings ?? []) as Finding[];
  const hasAuditReport = Boolean(auditOperation);
  const blocked = Boolean(report.blocking) || hasBlockingFindings(findings);
  const logs = appOperations.find((item) => item.kind === "logs" && item.status === "succeeded");
  const sourceConnected = (current?.source_status ?? "source_reconnect_required") === "connected";
  const activeDeployment = detail.data?.active_deployment ?? null;

  if (!application || !current) return null;

  async function accept(
    task: () => Promise<OperationSummary>,
    feedback?: { id: string; accepted: string },
  ) {
    setPending(true);
    try {
      const operation = await task();
      if (feedback) toast.success(feedback.accepted, { id: feedback.id });
      onAccepted(operation);
      await queryClient.invalidateQueries({ queryKey: appsKeys.operations(application!.id) });
      await queryClient.invalidateQueries({ queryKey: appsKeys.detail(application!.id) });
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "No se pudo aceptar la operación",
        feedback ? { id: feedback.id } : undefined,
      );
    } finally {
      setPending(false);
    }
  }

  return <>
    <Dialog open title={application.name} description={`${application.repository} · ${application.branch}`} onClose={onClose}>
      <div className="space-y-4">
        {!sourceConnected ? <Alert title="Fuente requiere reconexión">
          El repositorio ya no está autorizado por una instalación activa. Las inspecciones y despliegues nuevos están bloqueados; el lifecycle existente continúa.
          {canCreate ? <Button className="ml-2" size="sm" variant="secondary" disabled={pending} onClick={() => setSourceOpen(true)}>Elegir fuente</Button> : null}
        </Alert> : null}
        <div className="flex flex-wrap gap-1 border-b border-border pb-2">
          {(["overview", "audit", "deployments", "operations", "logs"] as Tab[]).map((item) =>
            <Button key={item} size="sm" variant={tab === item ? "default" : "secondary"} onClick={() => setTab(item)}>{item}</Button>)}
        </div>

        {tab === "overview" ? <div className="space-y-3">
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <div><dt className="text-muted-foreground">Estado</dt><dd>{current.status}</dd></div>
            <div><dt className="text-muted-foreground">Fuente</dt><dd>{current.repository} · {current.branch}</dd></div>
            <div><dt className="text-muted-foreground">Compose</dt><dd>{current.compose_file ?? report.compose ?? "Autodetectar"}</dd></div>
            <div><dt className="text-muted-foreground">Candidatos Compose</dt><dd>{(detail.data?.compose_candidates ?? []).join(", ") || "—"}</dd></div>
            <div><dt className="text-muted-foreground">Dominio</dt><dd>{current.domain ?? "—"}</dd></div>
            <div><dt className="text-muted-foreground">Ingress</dt><dd>{current.ingress_service ? `${current.ingress_service}:${current.ingress_port}` : "—"}</dd></div>
            <div><dt className="text-muted-foreground">Deployment activo</dt><dd>{activeDeployment?.id ?? "—"}</dd></div>
            <div><dt className="text-muted-foreground">Commit activo</dt><dd><code>{activeDeployment?.commit ?? "—"}</code></dd></div>
            <div><dt className="text-muted-foreground">Servicios</dt><dd>{(detail.data?.services ?? activeDeployment?.services ?? []).join(", ") || "—"}</dd></div>
            <div><dt className="text-muted-foreground">Source status</dt><dd>{current.source_status}</dd></div>
          </dl>
          <div className="flex flex-wrap gap-2">
            {canCreate ? <Button variant="secondary" disabled={pending || busy || !sourceConnected} onClick={() => {
              const id = `apps-inspect-${application.id}`;
              toast.loading("Enviando inspección…", { id });
              void accept(
                () => inspectApplication(application.id, newIdempotencyKey()),
                { id, accepted: "Inspección aceptada; podés seguir trabajando" },
              );
            }}>Inspeccionar</Button> : null}
            {canDeploy ? <Button disabled={busy || blocked || !sourceConnected} onClick={() => setDeployOpen(true)}>Desplegar</Button> : null}
            {canManage ? ACTIONS.map(([action, label]) => <Button key={action} variant="secondary" disabled={pending || !actionAllowed(current, action, busy)} onClick={() => void accept(() => runApplicationAction(application.id, action, newIdempotencyKey()))}>{label}</Button>) : null}
            {canDelete ? <Button variant="secondary" disabled={busy} className="text-destructive" onClick={() => setDeleteOpen(true)}>Eliminar</Button> : null}
          </div>
        </div> : null}

        {tab === "audit" ? findings.length ? <FindingsReport findings={findings} />
          : hasAuditReport ? <Alert title="Inspección aprobada">No se encontraron hallazgos.</Alert>
            : <Alert title="Sin informe">Ejecutá una inspección para obtener findings.</Alert> : null}
        {tab === "deployments" ? <div className="space-y-2">
          {(detail.data?.deployments ?? []).map((deployment) => <div key={deployment.id} className="rounded border border-border p-3 text-sm"><strong>{deployment.status}</strong> · commit <code>{deployment.commit ?? "—"}</code><br /><span className="text-muted-foreground">Servicios: {deployment.services.join(", ") || "—"}</span>{deployment.public_error ? <p>{deployment.public_error}</p> : null}</div>)}
          {(detail.data?.deployments ?? []).length === 0 ? <p className="text-sm text-muted-foreground">Sin deployments.</p> : null}
        </div> : null}
        {tab === "operations" ? <div className="space-y-2">
          {appOperations.map((operation) => <div key={operation.id} className="rounded border border-border p-3 text-sm"><strong>{operation.kind}</strong> · {operation.status} · {operation.phase}<br /><code className="text-xs">{operation.id}</code>{operation.public_error ? <p className="text-destructive">{operation.public_error}</p> : null}</div>)}
          {appOperations.length === 0 ? <p className="text-sm text-muted-foreground">No hay operaciones.</p> : null}
        </div> : null}
        {tab === "logs" ? <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded border border-border bg-muted p-3 text-xs">{String(logs?.result?.output ?? "Solicitá logs; el resultado redactado aparecerá aquí.")}</pre> : null}
      </div>
    </Dialog>
    <DeployDialog application={current} open={deployOpen} blocked={blocked} onClose={() => setDeployOpen(false)} onAccepted={(operation) => { setDeployOpen(false); onAccepted(operation); }} />
    <DeleteDialog application={current} open={deleteOpen} onClose={() => setDeleteOpen(false)} onAccepted={(operation) => { setDeleteOpen(false); onAccepted(operation); }} />
    <SourceDialog application={current} open={sourceOpen} onClose={() => setSourceOpen(false)} />
  </>;
}
