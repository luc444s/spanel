import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Tabs } from "@systutor/shell/ui/tabs";
import { toast } from "@systutor/shell/ui/toast";

import { domainsKeys, listRoutes } from "../../../domains/frontend/api";
import {
  appsKeys, getApplication, inspectApplication, listOperations, newIdempotencyKey,
  runApplicationAction, runServiceAction,
} from "../api";
import { actionAllowed, hasBlockingFindings } from "../logic";
import type { AppAction, Application, AuditReport, Finding, OperationSummary } from "../types";
import { DeleteDialog } from "./DeleteDialog";
import { DeployDialog } from "./DeployDialog";
import { FindingsReport } from "./FindingsReport";
import { ServiceCard } from "./ServiceCard";
import { ServiceDialog } from "./ServiceDialog";
import { ServiceOperations } from "./ServiceOperations";
import { SourceDialog } from "./SourceDialog";

type Tab = "overview" | "audit" | "deployments" | "operations";
const ACTIONS: Array<[AppAction, string]> = [
  ["refresh", "Actualizar todo"], ["down", "Down"],
];

export function ApplicationDetail({ application, canCreate, canManage, canManageDomains,
  canReadDomains, canReveal, canDeploy, canDelete, onClose, onAccepted }: {
  application: Application | null;
  canCreate: boolean;
  canManage: boolean;
  canManageDomains: boolean;
  canReadDomains: boolean;
  canReveal: boolean;
  canDeploy: boolean;
  canDelete: boolean;
  onClose: () => void;
  onAccepted: (operation: OperationSummary) => void;
}) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");
  const [serviceName, setServiceName] = useState<string | null>(null);
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
  const routesQuery = useQuery({
    queryKey: domainsKeys.routes(),
    queryFn: () => listRoutes(),
    enabled: Boolean(application) && canReadDomains,
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
  const sourceConnected = (current?.source_status ?? "source_reconnect_required") === "connected";
  const activeDeployment = detail.data?.active_deployment ?? null;
  const services = activeDeployment?.service_details ?? [];
  const selectedService = services.find((service) => service.name === serviceName) ?? null;
  const routes = routesQuery.data?.items ?? [];

  useEffect(() => {
    setServiceName(null);
  }, [application?.id, activeDeployment?.id]);

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

  const lastDeploy = activeDeployment?.finished_at ?? activeDeployment?.created_at
    ?? detail.data?.deployments?.[0]?.finished_at ?? null;
  const tabs = [
    {
      value: "overview" as const,
      label: "Resumen",
      content: <div className="space-y-4">
        <dl className="grid gap-3 text-sm sm:grid-cols-2">
          <div><dt className="text-muted-foreground">Estado</dt><dd>● {current.status}</dd></div>
          <div><dt className="text-muted-foreground">Fuente</dt><dd>GitHub · {sourceConnected ? "conectado" : "requiere reconexión"}</dd></div>
          <div><dt className="text-muted-foreground">Compose</dt><dd>{current.compose_file ?? report.compose ?? "Autodetectar"}</dd></div>
          <div><dt className="text-muted-foreground">Commit activo</dt><dd><code>{activeDeployment?.commit ?? "—"}</code></dd></div>
          <div><dt className="text-muted-foreground">Proyecto runtime</dt><dd><code>{detail.data?.runtime_project ?? "—"}</code></dd></div>
          <div><dt className="text-muted-foreground">Último deploy</dt><dd>{lastDeploy ? new Date(lastDeploy).toLocaleString() : "—"}</dd></div>
        </dl>
        <div className="space-y-2">
          <h3 className="text-sm font-medium">Servicios</h3>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {services.map((service) => <ServiceCard key={service.name} service={service}
              domainCount={canReadDomains
                ? routes.filter((route) => route.site_id === `${application.id}:${service.name}`).length
                : null}
              onOpen={() => setServiceName(service.name)} />)}
          </div>
          {services.length === 0 ? <p className="text-sm text-muted-foreground">Sin servicios en el deployment activo.</p> : null}
          <p className="text-xs text-muted-foreground">Doble clic sobre una tarjeta para administrarla.</p>
        </div>
      </div>,
    },
    {
      value: "audit" as const,
      label: "Auditoría",
      content: findings.length ? <FindingsReport findings={findings} />
        : hasAuditReport ? <Alert title="Inspección aprobada">No se encontraron hallazgos.</Alert>
          : <Alert title="Sin informe">Ejecutá una inspección para obtener findings.</Alert>,
    },
    {
      value: "deployments" as const,
      label: "Deployments",
      content: <div className="space-y-2">
        {(detail.data?.deployments ?? []).map((deployment) => <div key={deployment.id} className="rounded border border-border p-3 text-sm"><strong>{deployment.status}</strong> · commit <code>{deployment.commit ?? "—"}</code><br /><span className="text-muted-foreground">Servicios: {deployment.services.join(", ") || "—"}</span>{deployment.public_error ? <p>{deployment.public_error}</p> : null}</div>)}
        {(detail.data?.deployments ?? []).length === 0 ? <p className="text-sm text-muted-foreground">Sin deployments.</p> : null}
      </div>,
    },
    {
      value: "operations" as const,
      label: "Operaciones",
      content: <ServiceOperations operations={appOperations} />,
    },
  ];

  return <>
    <Dialog open title={application.name} description={`${application.repository} · ${application.branch}`}
      maxWidthClassName="max-w-[800px]" onClose={onClose}>
      <div className="space-y-4">
        {!sourceConnected ? <Alert title="Fuente requiere reconexión">
          El repositorio ya no está autorizado por una instalación activa. Las inspecciones y despliegues nuevos están bloqueados; el lifecycle existente continúa.
          {canCreate ? <Button className="ml-2" variant="secondary" disabled={pending} onClick={() => setSourceOpen(true)}>Elegir fuente</Button> : null}
        </Alert> : null}
        <Tabs value={tab} onChange={(value) => setTab(value as Tab)} tabs={tabs} />

        <div className="flex flex-wrap gap-2 border-t border-border pt-3">
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
          {canDelete ? <Button variant="secondary" disabled={busy} className="text-destructive" onClick={() => setDeleteOpen(true)}>Eliminar aplicación</Button> : null}
        </div>
      </div>
    </Dialog>
    {selectedService ? <ServiceDialog key={`${application.id}:${selectedService.name}`}
      applicationId={application.id} applicationName={application.name}
      service={selectedService} operations={appOperations}
      canManage={canManage} canReadDomains={canReadDomains}
      canManageDomains={canManageDomains} canReveal={canReveal}
      pending={pending} updatedAt={detail.dataUpdatedAt}
      onClose={() => setServiceName(null)}
      onAction={(action) => void accept(() =>
        runServiceAction(application.id, selectedService.name, action, newIdempotencyKey()))} /> : null}
    <DeployDialog application={current} open={deployOpen} blocked={blocked} onClose={() => setDeployOpen(false)} onAccepted={(operation) => { setDeployOpen(false); onAccepted(operation); }} />
    <DeleteDialog application={current} open={deleteOpen} onClose={() => setDeleteOpen(false)} onAccepted={(operation) => { setDeleteOpen(false); onAccepted(operation); }} />
    <SourceDialog application={current} open={sourceOpen} onClose={() => setSourceOpen(false)} />
  </>;
}
