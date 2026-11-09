import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { toast } from "@systutor/shell/ui/toast";
import { useAuthStore } from "../../../../../../apps/web/src/features/auth/store";

import { appsKeys, listApplications } from "../api";
import { canPlatformMutate } from "../logic";
import type { Application, Operation, OperationSummary } from "../types";
import { ApplicationDetail } from "../components/ApplicationDetail";
import { CreateApplicationDialog } from "../components/CreateApplicationDialog";
import { GithubConnectionCard } from "../components/GithubConnectionCard";
import { OperationTracker } from "../components/OperationTracker";
import { useGithubStatus } from "../useGithubStatus";

export default function AppsPage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((state) => state.permissions);
  const canConnect = permissions.includes("apps.github.connect");
  const canCreate = permissions.includes("apps.applications.create");
  const canManage = permissions.includes("apps.applications.manage");
  const platform = permissions.includes("core.platform.admin");
  const canDeploy = canPlatformMutate(permissions, "apps.deployments.create");
  const canDelete = canPlatformMutate(permissions, "apps.applications.delete");
  const [createOpen, setCreateOpen] = useState(false);
  const [selected, setSelected] = useState<Application | null>(null);
  const [tracked, setTracked] = useState<OperationSummary[]>([]);
  const [operations, setOperations] = useState<Operation[]>([]);
  const applications = useQuery({ queryKey: appsKeys.applications, queryFn: listApplications });
  const githubStatus = useGithubStatus(canConnect);

  useEffect(() => {
    const result = new URLSearchParams(window.location.search).get("github");
    if (result !== "installed" && result !== "connected" && result !== "error") return;
    window.history.replaceState({}, "", window.location.pathname + window.location.hash);
    void queryClient.invalidateQueries({ queryKey: appsKeys.status });
    void queryClient.invalidateQueries({ queryKey: appsKeys.installations });
    if (result === "error") {
      toast.error("No se pudo completar la conexión con GitHub");
    } else {
      toast.success(result === "installed" ? "GitHub App instalada; sincronizando repositorios" : "GitHub conectado");
    }
  }, [queryClient]);

  const updateOperation = useCallback((operation: Operation) => {
    setOperations((current) => [...current.filter((item) => item.id !== operation.id), operation]);
  }, []);
  function track(operation: OperationSummary) {
    setTracked((current) => current.some((item) => item.operation_id === operation.operation_id) ? current : [...current, operation]);
  }
  function accepted(application: Application, operation: OperationSummary) {
    setCreateOpen(false); setSelected(application); track(operation);
    void queryClient.invalidateQueries({ queryKey: appsKeys.applications });
  }

  if (applications.isLoading) return <div className="flex min-h-48 items-center justify-center text-sm text-muted-foreground">Cargando Apps…</div>;
  if (applications.error) return <Alert title="Error">No se pudieron cargar las aplicaciones.</Alert>;

  return <section className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div><h1 className="text-lg font-semibold text-foreground">Apps</h1><p className="text-sm text-muted-foreground">Despliegues Compose async desde GitHub</p></div>
      {canCreate ? <Button disabled={!githubStatus.data?.connected} onClick={() => setCreateOpen(true)}>Nueva aplicación</Button> : null}
    </div>
    <GithubConnectionCard canConnect={canConnect} statusQuery={githubStatus} />
    <Alert title="Modelo de seguridad">La auditoría estática no demuestra seguridad y Docker build ejecuta código no confiable. Los errores bloquean el despliegue sin bypass.</Alert>

    {tracked.length ? <div className="space-y-2"><h2 className="text-sm font-semibold">Operaciones activas y recientes</h2>{tracked.map((item) => <OperationTracker key={item.operation_id} operationId={item.operation_id} applicationId={item.application_id} onUpdate={updateOperation} />)}</div> : null}

    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {applications.data?.map((application) => <button key={application.id} type="button" className="rounded-md border border-border bg-card p-4 text-left hover:bg-muted" onClick={() => setSelected(application)}>
        <div className="flex items-center justify-between gap-2"><strong>{application.name}</strong><span className="rounded bg-muted px-2 py-0.5 text-xs">{application.status}</span></div>
        <p className="mt-2 text-sm text-muted-foreground">{application.repository} · {application.branch}</p>
        <p className="mt-1 text-xs text-muted-foreground">{application.domain ?? "Sin dominio"}</p>
      </button>)}
    </div>
    {applications.data?.length === 0 ? <p className="rounded border border-border p-4 text-sm text-muted-foreground">No hay aplicaciones.</p> : null}

    <CreateApplicationDialog open={createOpen} platform={platform} onClose={() => setCreateOpen(false)} onAccepted={accepted} />
    <ApplicationDetail application={selected} operations={operations} canCreate={canCreate} canManage={canManage} canDeploy={canDeploy} canDelete={canDelete} onClose={() => setSelected(null)} onAccepted={track} />
  </section>;
}
