import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  hostingKeys,
  deleteHostingSite,
  listHostingSites,
  listHostingProfiles,
  openHostingSiteDatabase,
  operateHostingSite,
  resetUserPassword,
  type HostingSite,
  type HostingSiteDatabase,
  type HostingSiteKind,
  type WordPressUser,
} from "../api";
import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { Pagination } from "@systutor/shell/ui/pagination";
import { toast } from "@systutor/shell/ui/toast";
import { useAuthStore } from "../../../../../../apps/web/src/features/auth/store";
import { useT } from "@/i18n/use-i18n";
import { SiteCard, kindLabel, stateLabel } from "../components/SiteCard";
import { CreateSiteDialog } from "../components/CreateSiteDialog";
import { ProfilesDialog } from "../components/ProfilesDialog";
import { SiteConfigDialog } from "../components/SiteConfigDialog";
import { DatabaseDialog } from "../components/DatabaseDialog";
import { BackupDialogs } from "../components/BackupDialogs";
import { LogsDialog, UsersDialog } from "../components/SiteDataDialogs";

type GeneratedPassword = {
  login: string;
  password: string;
};

const OPERATE_LABELS = { start: "Start", stop: "Stop", restart: "Restart" } as const;


/** Only the action that makes sense for the current state stays enabled. */
function actionAllowed(status: string, action: "start" | "stop" | "restart") {
  if (action === "start") return status !== "running";
  return status === "running";
}

export default function HostingPage() {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const permissions = useAuthStore((state) => state.permissions);
  const currentTenant = useAuthStore((state) => state.currentTenant);
  const platform = permissions.includes("core.platform.admin");
  const filemanagerEnabled = useAuthStore((state) => state.enabledPlugins.some(
    (plugin) => plugin.plugin_id === "filemanager"
  ));
  const canBrowseFiles = filemanagerEnabled && permissions.includes("filemanager.files.read");
  const canCreate = permissions.includes("hosting.sites.create");
  const canManage = permissions.includes("hosting.sites.manage");
  // Deleting a site is a platform capability, not a tenant one. The kernel
  // grants core.platform.admin only to the platform administrator, so the
  // button never appears for a normal tenant user.
  const canDelete =
    permissions.includes("core.platform.admin") &&
    permissions.includes("hosting.sites.delete");
  const canReadUsers = permissions.includes("hosting.users.read");
  const canReset = permissions.includes("hosting.users.password");
  // A.SPEC 0042: the profiles manager only for absolute platform control —
  // the SAME double-check as the delete-site button. Never is_superadmin.
  const canManageProfiles =
    permissions.includes("core.platform.admin") &&
    permissions.includes("hosting.profiles.manage");
  const [createOpen, setCreateOpen] = useState(false);
  const [profilesOpen, setProfilesOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<HostingSite | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [logsTarget, setLogsTarget] = useState<HostingSite | null>(null);
  const [configTarget, setConfigTarget] = useState<HostingSite | null>(null);
  const [databaseAccess, setDatabaseAccess] = useState<HostingSiteDatabase | null>(null);
  const [detailSite, setDetailSite] = useState<HostingSite | null>(null);
  const [selectedSite, setSelectedSite] = useState<HostingSite | null>(null);
  const [operateTarget, setOperateTarget] = useState<{
    site: HostingSite;
    action: "start" | "stop" | "restart";
  } | null>(null);
  const [resetTarget, setResetTarget] = useState<WordPressUser | null>(null);
  const [generated, setGenerated] = useState<GeneratedPassword | null>(null);
  const [createdUrl, setCreatedUrl] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const HOSTING_PAGE_SIZE = 9;

  // The create dialog owns its own form; the page only shows the result.
  function handleCreated(result: {
    kind: HostingSiteKind;
    domain: string;
    adminPassword: string | null;
  }) {
    setCreateOpen(false);
    if (result.kind === "static") {
      setCreatedUrl(`https://${result.domain}`);
    } else if (result.adminPassword) {
      setGenerated({ login: "admin", password: result.adminPassword });
    }
  }

  // The profiles list feeds BOTH the create-site selector and the manager.
  const profilesQuery = useQuery({
    queryKey: hostingKeys.profiles,
    queryFn: listHostingProfiles,
    enabled: createOpen || profilesOpen,
  });
  const profiles = profilesQuery.data?.profiles ?? [];

  const sitesQuery = useQuery({
    queryKey: [...hostingKeys.sites, platform ? "all" : "tenant"],
    queryFn: () => listHostingSites(platform),
  });

  const operateMutation = useMutation({
    mutationFn: ({ siteId, action }: { siteId: string; action: "start" | "stop" | "restart" }) =>
      operateHostingSite(siteId, action),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: hostingKeys.sites });
    },
  });

  /**
   * A.SPEC 0043: the confirm dialog closes at the submit instant; the
   * operation runs in background and toast.promise reports the result.
   * Errors keep the real backend message (I2).
   */
  function submitOperate(siteId: string, action: "start" | "stop" | "restart") {
    setOperateTarget(null);
    toast.promise(operateMutation.mutateAsync({ siteId, action }), {
      loading: t("Applying {action}…", { action: t(OPERATE_LABELS[action]) }),
      success: t("Site {action} executed", {
        action: t(OPERATE_LABELS[action]).toLowerCase(),
      }),
      error: (error: Error) =>
        error.message ||
        t("Could not apply {action}", { action: t(OPERATE_LABELS[action]) }),
    });
  }

  const deleteMutation = useMutation({
    mutationFn: deleteHostingSite,
    onSuccess: async () => {
      setDeleteTarget(null); setConfirmation(""); setSelectedSite(null); setLogsTarget(null);
      setConfigTarget(null); setDetailSite(null); setOperateTarget(null);
      setDatabaseAccess(null);
      await queryClient.invalidateQueries({ queryKey: hostingKeys.sites });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const databaseMutation = useMutation({
    mutationFn: openHostingSiteDatabase,
    onSuccess: (data) => setDatabaseAccess(data),
    onError: (error: Error) => toast.error(error.message),
  });

  /** Closing drops the credential from state AND from the mutation cache. */
  function closeDatabase() {
    setDatabaseAccess(null);
    databaseMutation.reset();
  }

  const resetMutation = useMutation({
    mutationFn: ({ siteId, userId }: { siteId: string; userId: number }) =>
      resetUserPassword(siteId, userId),
    onSuccess: (data) => {
      setResetTarget(null);
      // The generated password is shown once, here. It is not stored in the
      // cache or in any other query.
      if (data.password) {
        setGenerated({ login: data.login, password: data.password });
      }
      toast.success(t("Password reset"));
    },
    onError: (error: Error) => {
      setResetTarget(null);
      toast.error(error.message);
    },
  });

  async function copyPassword() {
    if (!generated) return;
    try {
      await navigator.clipboard.writeText(generated.password);
      toast.success(t("Password copied"));
    } catch {
      toast.error(t("Could not copy the password"));
    }
  }

  if (sitesQuery.isLoading) {
    return (
      <div className="flex min-h-[200px] items-center justify-center text-sm text-muted-foreground">
        {t("Loading...")}
      </div>
    );
  }

  if (sitesQuery.error) {
    return <Alert title={t("Error")}>{t("Could not load sites.")}</Alert>;
  }

  const sites = sitesQuery.data?.sites ?? [];
  const totalPages = Math.max(1, Math.ceil(sites.length / HOSTING_PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pagedSites = sites.slice(
    (currentPage - 1) * HOSTING_PAGE_SIZE,
    currentPage * HOSTING_PAGE_SIZE,
  );

  // The list is the source of truth: after an action we refetch it, and the
  // open dialog must show the new status without closing and reopening.
  const detail = detailSite ? sites.find((site) => site.id === detailSite.id) ?? detailSite : null;
  // A static site has no WordPress surface: users, config, database and
  // phpMyAdmin are not offered. Logs, lifecycle, filemanager and delete stay.
  const isStatic = detail?.kind === "static";
  const isOwned = Boolean(
    detail && (!detail.tenant_id || detail.tenant_id === currentTenant?.id)
  );

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-foreground">{t("Hosting")}</h1>
        <div className="flex gap-2">
          {canManageProfiles ? (
            <Button variant="secondary" onClick={() => setProfilesOpen(true)}>
              {t("Profiles")}
            </Button>
          ) : null}
          {canCreate ? <Button onClick={() => setCreateOpen(true)}>{t("Create site")}</Button> : null}
        </div>
      </div>

      {sites.length === 0 ? (
        <p className="rounded-md border border-border bg-card px-3 py-4 text-sm text-muted-foreground">
          {t("No sites are assigned to this tenant.")}
        </p>
      ) : (
        <div className="flex min-h-[32rem] flex-col gap-4 md:min-h-[36rem]">
          <div className="flex-1">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
              {pagedSites.map((site) => (
                <SiteCard
                  key={site.id}
                  site={site}
                  readOnly={Boolean(site.tenant_id && site.tenant_id !== currentTenant?.id)}
                  onOpen={setDetailSite}
                />
              ))}
            </div>
          </div>
          <Pagination page={currentPage} totalPages={totalPages} onChange={setPage} />
        </div>
      )}

      <Dialog
        open={Boolean(detail)}
        title={detail?.container_name ?? ""}
        description={detail?.domain ?? t("No domain")}
        onClose={() => {
          setDetailSite(null);
          setDatabaseAccess(null);
        }}
      >
        {detail ? (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <dt className="text-xs text-muted-foreground">{t("Type")}</dt>
                <dd className="text-foreground">{kindLabel(detail.kind)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">{t("Status")}</dt>
                <dd className="text-foreground">{stateLabel(detail.status)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">{t("Managed")}</dt>
                <dd className="text-foreground">
                  {isOwned ? (detail.managed ? t("Yes") : t("No")) : t("Read only")}
                </dd>
              </div>
              {detail.tenant_name ? (
                <div>
                  <dt className="text-xs text-muted-foreground">{t("Tenant")}</dt>
                  <dd className="text-foreground">{detail.tenant_name}</dd>
                </div>
              ) : null}
              {isStatic && detail.profile ? (
                <div>
                  <dt className="text-xs text-muted-foreground">{t("Profile")}</dt>
                  <dd className="font-mono text-foreground">{detail.profile}</dd>
                </div>
              ) : null}
              <div className="col-span-2">
                <dt className="text-xs text-muted-foreground">{t("ID")}</dt>
                <dd className="break-all font-mono text-xs text-foreground">{detail.id}</dd>
              </div>
            </dl>

            <div className="grid grid-cols-2 gap-2">
              {isOwned && canReadUsers && !isStatic ? (
                <Button variant="secondary" onClick={() => setSelectedSite(detail)}>
                  {t("View users")}
                </Button>
              ) : null}
              {isOwned ? (
                <Button variant="secondary" onClick={() => setLogsTarget(detail)}>
                  {t("Logs")}
                </Button>
              ) : null}
              {isOwned && canBrowseFiles && detail.status === "running" ? (
                <Button variant="secondary" onClick={() => navigate(`/app/filemanager/hosting/${encodeURIComponent(detail.id)}`)}>
                  {t("File manager")}
                </Button>
              ) : null}
              {isOwned && !isStatic ? (
                <Button
                  variant="secondary"
                  disabled={detail.status === "missing"}
                  onClick={() => setConfigTarget(detail)}
                >
                  {t("Configuration")}
                </Button>
              ) : null}
              {isOwned && canManage && !isStatic ? (
                <Button
                  variant="secondary"
                  disabled={detail.status === "missing" || databaseMutation.isPending}
                  onClick={() => databaseMutation.mutate(detail.id)}
                >
                  {databaseMutation.isPending ? t("Reading...") : t("Database")}
                </Button>
              ) : null}
              {isOwned && canManage ? <BackupDialogs site={detail} /> : null}
              {isOwned && canManage
                ? (["start", "stop", "restart"] as const).map((action) => (
                    <Button
                      key={action}
                      variant="secondary"
                      disabled={
                        !detail.managed ||
                        operateMutation.isPending ||
                        !actionAllowed(detail.status, action)
                      }
                      onClick={() => setOperateTarget({ site: detail, action })}
                    >
                      {t(OPERATE_LABELS[action])}
                    </Button>
                  ))
                : null}
              {isOwned && canDelete ? (
                <Button
                  variant="secondary"
                  disabled={!detail.managed}
                  className="border-destructive/60 text-destructive hover:bg-destructive/10"
                  onClick={() => setDeleteTarget(detail)}
                >
                  {t("Delete")}
                </Button>
              ) : null}
            </div>
          </div>
        ) : null}
      </Dialog>

      <UsersDialog
        site={selectedSite}
        canReset={canReset}
        onClose={() => setSelectedSite(null)}
        onReset={setResetTarget}
      />

      <CreateSiteDialog
        open={createOpen && canCreate}
        profiles={canCreate ? profiles : []}
        onClose={() => setCreateOpen(false)}
        onCreated={handleCreated}
      />

      <ProfilesDialog
        open={profilesOpen && canManageProfiles}
        profiles={profiles}
        onClose={() => setProfilesOpen(false)}
      />

      <Dialog
        open={Boolean(createdUrl)}
        title={t("Site created")}
        description={createdUrl ?? undefined}
        onClose={() => setCreatedUrl(null)}
        zIndexClassName="z-[1300]"
        maxWidthClassName="max-w-md"
      >
        <div className="space-y-3">
          <p className="text-sm text-foreground">{t("The static site is now available at:")}</p>
          <code className="block overflow-x-auto rounded-md border border-border bg-muted px-3 py-2 font-mono text-sm">
            {createdUrl ?? ""}
          </code>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={() => setCreatedUrl(null)}>
              {t("Close")}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => {
                if (createdUrl) window.open(createdUrl, "_blank", "noopener,noreferrer");
              }}
            >
              {t("Open site")}
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog
        open={Boolean(operateTarget)}
        title={
          operateTarget
            ? t("{action} site", { action: t(OPERATE_LABELS[operateTarget.action]) })
            : ""
        }
        description={operateTarget?.site.container_name}
        onClose={() => setOperateTarget(null)}
        zIndexClassName="z-[1100]"
        maxWidthClassName="max-w-md"
      >
        <div className="space-y-4 text-sm text-foreground">
          <p>
            {operateTarget?.action === "stop"
              ? t("The site containers will be stopped. Data is not lost.")
              : operateTarget?.action === "restart"
                ? t("The site containers will be restarted.")
                : t("The site containers will be started.")}
          </p>
          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              disabled={operateMutation.isPending}
              onClick={() => setOperateTarget(null)}
            >
              {t("Cancel")}
            </Button>
            <Button
              disabled={operateMutation.isPending}
              onClick={() => {
                if (!operateTarget) return;
                submitOperate(operateTarget.site.id, operateTarget.action);
              }}
            >
              {operateMutation.isPending
                ? t("Processing...")
                : operateTarget
                  ? t(OPERATE_LABELS[operateTarget.action])
                  : ""}
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog open={Boolean(deleteTarget && canDelete)} title={t("Delete site permanently")}
        onClose={() => setDeleteTarget(null)} zIndexClassName="z-[1100]" maxWidthClassName="max-w-md">
        <p className="text-sm">{t("The containers and data will be deleted with no possibility of recovery. Type {name} to confirm.", { name: deleteTarget?.container_name ?? "" })}</p>
        <Input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
        <Button disabled={!deleteTarget || confirmation !== deleteTarget.container_name || deleteMutation.isPending}
          onClick={() => { if (deleteTarget) deleteMutation.mutate(deleteTarget.id); }}>{t("Delete")}</Button>
      </Dialog>

      <LogsDialog site={logsTarget} onClose={() => setLogsTarget(null)} />

      <SiteConfigDialog
        site={configTarget}
        canManage={canManage && Boolean(configTarget?.tenant_id === currentTenant?.id || !configTarget?.tenant_id)}
        onClose={() => setConfigTarget(null)}
      />

      <DatabaseDialog access={databaseAccess} onClose={() => setDatabaseAccess(null)} />

      <Dialog
        open={Boolean(resetTarget)}
        title={t("Reset password")}
        description={
          resetTarget
            ? t("{login} (ID {id}) on {site}", {
                login: resetTarget.login,
                id: resetTarget.id,
                site: selectedSite?.container_name ?? "",
              })
            : undefined
        }
        onClose={() => setResetTarget(null)}
        zIndexClassName="z-[1200]"
        maxWidthClassName="max-w-md"
      >
        <div className="space-y-3 text-sm text-foreground">
          <p>
            {t("A new password will be generated and shown only once. The user will lose their previous password.")}
          </p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={() => setResetTarget(null)}>
              {t("Cancel")}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={resetMutation.isPending}
              onClick={() => {
                if (!selectedSite || !resetTarget) return;
                resetMutation.mutate({ siteId: selectedSite.id, userId: resetTarget.id });
              }}
            >
              {resetMutation.isPending ? "..." : t("Reset")}
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog
        open={Boolean(generated)}
        title={t("Generated password")}
        description={generated ? t("User {login}", { login: generated.login }) : undefined}
        onClose={() => setGenerated(null)}
        zIndexClassName="z-[1300]"
        maxWidthClassName="max-w-md"
      >
        <div className="space-y-3">
          <Alert title={t("Shown only once")}>
            {t("Copy this password now. Once this dialog is closed, it cannot be viewed again.")}
          </Alert>
          <div className="flex items-center gap-2">
            <code className="flex-1 overflow-x-auto rounded-md border border-border bg-muted px-3 py-2 font-mono text-sm">
              {generated?.password ?? ""}
            </code>
            <Button type="button" size="sm" onClick={() => void copyPassword()}>
              {t("Copy")}
            </Button>
          </div>
          <div className="flex justify-end">
            <Button type="button" variant="secondary" size="sm" onClick={() => setGenerated(null)}>
              {t("Close")}
            </Button>
          </div>
        </div>
      </Dialog>
    </section>
  );
}
