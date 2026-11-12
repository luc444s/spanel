import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { toast } from "@systutor/shell/ui/toast";
import { useAuthStore } from "../../../../../../apps/web/src/features/auth/store";
import { useT } from "@/i18n/use-i18n";

import { appsKeys, listApplications } from "../api";
import { domainsKeys, listRoutes } from "../../../domains/frontend/api";
import { canDeploy, canPlatformMutate, isOwnedByTenant } from "../logic";
import type { Application, OperationSummary } from "../types";
import { ApplicationDetail } from "../components/ApplicationDetail";
import { CreateApplicationDialog } from "../components/CreateApplicationDialog";
import { GithubConnectionCard } from "../components/GithubConnectionCard";
import { useGithubStatus } from "../useGithubStatus";

export default function AppsPage() {
  const t = useT();
  const queryClient = useQueryClient();
  const permissions = useAuthStore((state) => state.permissions);
  const currentTenant = useAuthStore((state) => state.currentTenant);
  const canConnect = permissions.includes("apps.github.connect");
  const canCreate = permissions.includes("apps.applications.create");
  const canManage = permissions.includes("apps.applications.manage");
  const canManageDomains = permissions.includes("domains.domains.manage");
  const canReadDomains = permissions.includes("domains.domains.read");
  const canReveal = permissions.includes("apps.secrets.read");
  const platform = permissions.includes("core.platform.admin");
  const canDeployApp = canDeploy(permissions);
  const canDelete = canPlatformMutate(permissions, "apps.applications.delete");
  const [createOpen, setCreateOpen] = useState(false);
  const [selected, setSelected] = useState<Application | null>(null);
  const applications = useQuery({
    queryKey: [...appsKeys.applications, platform ? "all" : "tenant"],
    queryFn: () => listApplications(platform),
  });
  const routesQuery = useQuery({
    queryKey: domainsKeys.routes(),
    queryFn: () => listRoutes(),
    enabled: canReadDomains,
  });
  const routes = routesQuery.data?.items ?? [];
  const githubStatus = useGithubStatus(canConnect);

  useEffect(() => {
    const result = new URLSearchParams(window.location.search).get("github");
    if (result !== "installed" && result !== "connected" && result !== "error") return;
    window.history.replaceState({}, "", window.location.pathname + window.location.hash);
    void queryClient.invalidateQueries({ queryKey: appsKeys.status });
    void queryClient.invalidateQueries({ queryKey: appsKeys.installations });
    if (result === "error") {
      toast.error(t("Could not complete the GitHub connection"));
    } else {
      toast.success(result === "installed"
        ? t("GitHub App installed; syncing repositories")
        : t("GitHub connected"));
    }
  }, [queryClient]);

  function accepted(application: Application) {
    setCreateOpen(false); setSelected(application);
    void queryClient.invalidateQueries({ queryKey: appsKeys.applications });
  }
  function operationAccepted(operation: OperationSummary) {
    void queryClient.invalidateQueries({ queryKey: appsKeys.applications });
    if (operation.application_id) {
      void queryClient.invalidateQueries({ queryKey: appsKeys.detail(operation.application_id) });
      void queryClient.invalidateQueries({ queryKey: appsKeys.operations(operation.application_id) });
    }
  }

  if (applications.isLoading) return <div className="flex min-h-48 items-center justify-center text-sm text-muted-foreground">{t("Loading Apps…")}</div>;
  if (applications.error) return <Alert title={t("Error")}>{t("Could not load applications.")}</Alert>;

  return <section className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div><h1 className="text-lg font-semibold text-foreground">{t("Apps")}</h1><p className="text-sm text-muted-foreground">{t("Async Compose deployments from GitHub")}</p></div>
      {canCreate ? <Button disabled={!githubStatus.data?.connected} onClick={() => setCreateOpen(true)}>{t("New application")}</Button> : null}
    </div>
    <GithubConnectionCard canConnect={canConnect} statusQuery={githubStatus} />

    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {applications.data?.map((application) => {
        const owned = isOwnedByTenant(application.tenant_id, currentTenant?.id);
        const appRoutes = routes.filter((route) =>
          route.site_id.startsWith(`${application.id}:`));
        const domainLabel = !canReadDomains
          ? application.domain ?? t("No domain")
          : routesQuery.isLoading
            ? t("Loading domains...")
            : appRoutes.length === 0
              ? application.domain ?? t("No domains")
              : appRoutes.length === 1
                ? `${appRoutes[0].domain}${appRoutes[0].path === "/" ? "" : appRoutes[0].path}`
                : t("{count} domains", { count: appRoutes.length });
        return <button key={application.id} type="button"
          aria-disabled={!owned}
          className={`rounded-md border border-border bg-card p-4 text-left ${owned ? "hover:bg-muted" : "cursor-default"}`}
          onClick={() => { if (owned) setSelected(application); }}>
          <div className="flex items-center justify-between gap-2"><strong>{application.name}</strong><span className="rounded bg-muted px-2 py-0.5 text-xs">{application.status}</span></div>
          <p className="mt-2 text-sm text-muted-foreground">{application.repository} · {application.branch}</p>
          <p className="mt-1 text-xs text-muted-foreground">{domainLabel}</p>
          {application.tenant_name ? <p className="mt-1 text-xs text-muted-foreground">{t("Tenant")}: {application.tenant_name}</p> : null}
          {!owned ? <p className="mt-2 text-xs font-medium text-muted-foreground">{t("Read only")}</p> : null}
        </button>;
      })}
    </div>
    {applications.data?.length === 0 ? <p className="rounded border border-border p-4 text-sm text-muted-foreground">{t("No applications.")}</p> : null}

    <CreateApplicationDialog open={createOpen} platform={platform} onClose={() => setCreateOpen(false)} onAccepted={accepted} />
    <ApplicationDetail key={selected?.id ?? "no-application"} application={selected} canCreate={canCreate} canManage={canManage}
      canManageDomains={canManageDomains} canReadDomains={canReadDomains}
      canReveal={canReveal} canDeploy={canDeployApp} canDelete={canDelete}
      onClose={() => setSelected(null)} onAccepted={operationAccepted} />
  </section>;
}
