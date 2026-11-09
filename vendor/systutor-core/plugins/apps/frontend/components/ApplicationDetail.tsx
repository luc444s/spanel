import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { toast } from "@systutor/shell/ui/toast";

import {
  appsKeys, inspectApplication, listDeployments, newIdempotencyKey, reconnectSource,
  runApplicationAction,
} from "../api";
import { actionAllowed, hasBlockingFindings } from "../logic";
import type { AppAction, Application, AuditReport, Finding, Operation, OperationSummary } from "../types";
import { DeleteDialog } from "./DeleteDialog";
import { DeployDialog } from "./DeployDialog";
import { FindingsReport } from "./FindingsReport";

type Tab = "overview" | "audit" | "deployments" | "operations" | "logs";
const ACTIONS: Array<[AppAction, string]> = [
  ["refresh", "Actualizar"], ["logs", "Solicitar logs"], ["start", "Iniciar"],
  ["stop", "Detener"], ["restart", "Reiniciar"], ["down", "Down"],
];

export function ApplicationDetail({ application, operations, currentUserId, connected, canCreate,
  canManage, canDeploy, canDelete, onClose, onAccepted }: {
  application: Application | null;
  operations: Operation[];
  currentUserId: string;
  connected: boolean;
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
  const [pending, setPending] = useState(false);
  const deployments = useQuery({
    queryKey: appsKeys.deployments(application?.id ?? "idle"),
    queryFn: () => listDeployments(application!.id),
    enabled: Boolean(application && tab === "deployments"),
  });
  const appOperations = useMemo(() => operations.filter((item) => item.application_id === application?.id), [application?.id, operations]);
  const busy = appOperations.some((item) => !["succeeded", "failed", "interrupted"].includes(item.status));
  const auditOperation = [...appOperations].reverse().find((item) => {
    const candidate = (item.result ?? item.report) as AuditReport | null;
    return Array.isArray(candidate?.findings);
  });
  const report = (auditOperation?.result ?? auditOperation?.report ?? {}) as AuditReport;
  const findings = (report.findings ?? []) as Finding[];
  const blocked = Boolean(report.blocking) || hasBlockingFindings(findings);
  const logs = [...appOperations].reverse().find((item) => item.kind === "logs" && item.status === "succeeded");
  const lastLifecycle = [...appOperations].reverse().find((item) =>
    item.status === "succeeded" && ["start", "stop", "restart", "down"].includes(item.kind));
  const effectiveApplication = lastLifecycle
    ? { ...application, status: ["stop", "down"].includes(lastLifecycle.kind) ? "stopped" : "running" }
    : application;
  const sourceOwnedByCurrentUser = application?.source_connection_user_id === currentUserId;

  if (!application) return null;

  async function accept(task: () => Promise<OperationSummary>) {
    setPending(true);
    try { onAccepted(await task()); }
    catch (error) { toast.error(error instanceof Error ? error.message : "No se pudo aceptar la operación"); }
    finally { setPending(false); }
  }

  async function reconnect() {
    setPending(true);
    try {
      await reconnectSource(application.id, currentUserId);
      await queryClient.invalidateQueries({ queryKey: appsKeys.application(application.id) });
      await queryClient.invalidateQueries({ queryKey: appsKeys.applications });
      toast.success("Fuente asociada a tu conexión GitHub");
    } catch (error) { toast.error(error instanceof Error ? error.message : "No se pudo reconectar la fuente"); }
    finally { setPending(false); }
  }

  return <>
    <Dialog open title={application.name} description={`${application.repository} · ${application.branch}`} onClose={onClose}>
      <div className="space-y-4">
        {!sourceOwnedByCurrentUser ? <Alert title="Fuente vinculada a otra conexión">
          Identidad interna: <code>{application.source_connection_user_id}</code>
          {canCreate && connected ? <Button className="ml-2" size="sm" variant="secondary" disabled={pending || busy} onClick={() => void reconnect()}>Reconectar fuente</Button> : null}
        </Alert> : null}
        <div className="flex flex-wrap gap-1 border-b border-border pb-2">
          {(["overview", "audit", "deployments", "operations", "logs"] as Tab[]).map((item) =>
            <Button key={item} size="sm" variant={tab === item ? "default" : "secondary"} onClick={() => setTab(item)}>{item}</Button>)}
        </div>

        {tab === "overview" ? <div className="space-y-3">
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <div><dt className="text-muted-foreground">Estado persistido</dt><dd>{application.status}</dd></div>
            <div><dt className="text-muted-foreground">Compose</dt><dd>{application.compose_file ?? report.compose ?? "Autodetectar"}</dd></div>
            <div><dt className="text-muted-foreground">Dominio</dt><dd>{application.domain ?? "—"}</dd></div>
            <div><dt className="text-muted-foreground">Ingress</dt><dd>{application.ingress_service ? `${application.ingress_service}:${application.ingress_port}` : "—"}</dd></div>
            <div><dt className="text-muted-foreground">Commit activo</dt><dd>{deployments.data?.find((item) => item.status === "running")?.commit ?? "No expuesto por detalle"}</dd></div>
            <div><dt className="text-muted-foreground">Servicios</dt><dd>No expuestos por la API de detalle</dd></div>
          </dl>
          <div className="flex flex-wrap gap-2">
            {canCreate ? <Button variant="secondary" disabled={pending || busy || !connected || !sourceOwnedByCurrentUser} onClick={() => void accept(() => inspectApplication(application.id, newIdempotencyKey()))}>Inspeccionar</Button> : null}
            {canDeploy ? <Button disabled={busy || blocked || !connected || !sourceOwnedByCurrentUser} onClick={() => setDeployOpen(true)}>Desplegar</Button> : null}
            {canManage ? ACTIONS.map(([action, label]) => <Button key={action} variant="secondary" disabled={pending || !actionAllowed(effectiveApplication, action, busy)} onClick={() => void accept(() => runApplicationAction(application.id, action, newIdempotencyKey()))}>{label}</Button>) : null}
            {canDelete ? <Button variant="secondary" disabled={busy} className="text-destructive" onClick={() => setDeleteOpen(true)}>Eliminar</Button> : null}
          </div>
        </div> : null}

        {tab === "audit" ? findings.length ? <FindingsReport findings={findings} /> : <Alert title="Sin informe">Ejecutá una inspección para obtener findings.</Alert> : null}
        {tab === "deployments" ? <div className="space-y-2">
          {deployments.data?.map((deployment) => <div key={deployment.id} className="rounded border border-border p-3 text-sm"><strong>{deployment.status}</strong> · commit <code>{deployment.commit ?? "—"}</code><br /><span className="text-muted-foreground">tree {deployment.tree_hash ?? "—"}</span>{deployment.public_error ? <p>{deployment.public_error}</p> : null}</div>)}
          {deployments.data?.length === 0 ? <p className="text-sm text-muted-foreground">Sin deployments.</p> : null}
        </div> : null}
        {tab === "operations" ? <div className="space-y-2">
          {appOperations.map((operation) => <div key={operation.id} className="rounded border border-border p-3 text-sm"><strong>{operation.kind}</strong> · {operation.status} · {operation.phase}<br /><code className="text-xs">{operation.id}</code>{operation.public_error ? <p className="text-destructive">{operation.public_error}</p> : null}</div>)}
          {appOperations.length === 0 ? <p className="text-sm text-muted-foreground">No hay operaciones observadas en esta sesión.</p> : null}
        </div> : null}
        {tab === "logs" ? <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded border border-border bg-muted p-3 text-xs">{String(logs?.result?.output ?? "Solicitá logs; el resultado redactado aparecerá aquí.")}</pre> : null}
      </div>
    </Dialog>
    <DeployDialog application={application} open={deployOpen} blocked={blocked} onClose={() => setDeployOpen(false)} onAccepted={(operation) => { setDeployOpen(false); onAccepted(operation); }} />
    <DeleteDialog application={application} open={deleteOpen} onClose={() => setDeleteOpen(false)} onAccepted={(operation) => { setDeleteOpen(false); onAccepted(operation); }} />
  </>;
}
