import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import {
  hostingKeys,
  createHostingSite,
  operateHostingSite,
  deleteHostingSite,
  listHostingSites,
  resetUserPassword,
  type HostingSite,
  type WordPressUser,
} from "../api";
import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { toast } from "@systutor/shell/ui/toast";
import { Input } from "@systutor/shell/ui/input";
import { useAuthStore } from "../../../../../../apps/web/src/features/auth/store";
import { SiteCard, stateLabel } from "../components/SiteCard";
import { LogsDialog, UsersDialog } from "../components/SiteDataDialogs";

type GeneratedPassword = {
  login: string;
  password: string;
};

const OPERATE_LABELS = { start: "Iniciar", stop: "Detener", restart: "Reiniciar" } as const;

/** Only the action that makes sense for the current state stays enabled. */
function actionAllowed(status: string, action: "start" | "stop" | "restart") {
  if (action === "start") return status !== "running";
  return status === "running";
}

export default function HostingPage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((state) => state.permissions);
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
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [email, setEmail] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<HostingSite | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [logsTarget, setLogsTarget] = useState<HostingSite | null>(null);
  const [detailSite, setDetailSite] = useState<HostingSite | null>(null);
  const [selectedSite, setSelectedSite] = useState<HostingSite | null>(null);
  const [operateTarget, setOperateTarget] = useState<{
    site: HostingSite;
    action: "start" | "stop" | "restart";
  } | null>(null);
  const [resetTarget, setResetTarget] = useState<WordPressUser | null>(null);
  const [generated, setGenerated] = useState<GeneratedPassword | null>(null);

  const sitesQuery = useQuery({
    queryKey: hostingKeys.sites,
    queryFn: listHostingSites,
  });

  const createMutation = useMutation({
    mutationFn: createHostingSite,
    onSuccess: async (data) => {
      await queryClient.invalidateQueries({ queryKey: hostingKeys.sites });
      setCreateOpen(false);
      setName(""); setDomain(""); setEmail("");
      setGenerated({ login: "admin", password: data.admin_password });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const operateMutation = useMutation({
    mutationFn: ({ siteId, action }: { siteId: string; action: "start" | "stop" | "restart" }) =>
      operateHostingSite(siteId, action),
    onSuccess: async () => {
      setOperateTarget(null);
      await queryClient.invalidateQueries({ queryKey: hostingKeys.sites });
    },
    onError: (error: Error) => {
      setOperateTarget(null);
      toast.error(error.message);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteHostingSite,
    onSuccess: async () => {
      setDeleteTarget(null); setConfirmation(""); setSelectedSite(null); setLogsTarget(null);
      setDetailSite(null); setOperateTarget(null);
      await queryClient.invalidateQueries({ queryKey: hostingKeys.sites });
    },
    onError: (error: Error) => toast.error(error.message),
  });

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
      toast.success("Contraseña restablecida");
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
      toast.success("Contraseña copiada");
    } catch {
      toast.error("No se pudo copiar la contraseña");
    }
  }

  if (sitesQuery.isLoading) {
    return (
      <div className="flex min-h-[200px] items-center justify-center text-sm text-muted-foreground">
        Cargando...
      </div>
    );
  }

  if (sitesQuery.error) {
    return <Alert title="Error">No se pudieron cargar los sitios.</Alert>;
  }

  const sites = sitesQuery.data?.sites ?? [];
  // The list is the source of truth: after an action we refetch it, and the
  // open dialog must show the new status without closing and reopening.
  const detail = detailSite ? sites.find((site) => site.id === detailSite.id) ?? detailSite : null;

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-foreground">Hosting</h1>
        {canCreate ? <Button onClick={() => setCreateOpen(true)}>Crear sitio</Button> : null}
      </div>

      {sites.length === 0 ? (
        <p className="rounded-md border border-border bg-card px-3 py-4 text-sm text-muted-foreground">
          No hay sitios WordPress asignados a este tenant.
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {sites.map((site) => (
            <SiteCard key={site.id} site={site} onOpen={setDetailSite} />
          ))}
        </div>
      )}

      <Dialog
        open={Boolean(detail)}
        title={detail?.container_name ?? ""}
        description={detail?.domain ?? "Sin dominio"}
        onClose={() => setDetailSite(null)}
      >
        {detail ? (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <dt className="text-xs text-muted-foreground">Estado</dt>
                <dd className="text-foreground">{stateLabel(detail.status)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Gestionable</dt>
                <dd className="text-foreground">{detail.managed ? "Sí" : "No"}</dd>
              </div>
              <div className="col-span-2">
                <dt className="text-xs text-muted-foreground">ID</dt>
                <dd className="break-all font-mono text-xs text-foreground">{detail.id}</dd>
              </div>
            </dl>

            <div className="grid grid-cols-2 gap-2">
              {canReadUsers ? (
                <Button variant="secondary" onClick={() => setSelectedSite(detail)}>
                  Ver usuarios
                </Button>
              ) : null}
              <Button variant="secondary" onClick={() => setLogsTarget(detail)}>
                Logs
              </Button>
              {canManage
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
                      {OPERATE_LABELS[action]}
                    </Button>
                  ))
                : null}
              {canDelete ? (
                <Button
                  variant="secondary"
                  disabled={!detail.managed}
                  className="border-destructive/60 text-destructive hover:bg-destructive/10"
                  onClick={() => setDeleteTarget(detail)}
                >
                  Eliminar
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

      <Dialog
        open={createOpen && canCreate}
        title="Crear sitio WordPress"
        onClose={() => setCreateOpen(false)}
      >
        <form className="space-y-3" onSubmit={(event: FormEvent) => {
          event.preventDefault(); createMutation.mutate({ name, domain, admin_email: email });
        }}>
          <Input required placeholder="Nombre" value={name} onChange={(event) => setName(event.target.value)} />
          <Input required placeholder="Dominio" value={domain} onChange={(event) => setDomain(event.target.value)} />
          <Input required type="email" placeholder="Email del administrador" value={email}
            onChange={(event) => setEmail(event.target.value)} />
          <Button type="submit" disabled={createMutation.isPending}>Crear</Button>
        </form>
      </Dialog>

      <Dialog
        open={Boolean(operateTarget)}
        title={operateTarget ? `${OPERATE_LABELS[operateTarget.action]} sitio` : ""}
        description={operateTarget?.site.container_name}
        onClose={() => setOperateTarget(null)}
        zIndexClassName="z-[1100]"
        maxWidthClassName="max-w-md"
      >
        <div className="space-y-4 text-sm text-foreground">
          <p>
            {operateTarget?.action === "stop"
              ? "Se detendrán los contenedores del sitio. Los datos no se pierden."
              : operateTarget?.action === "restart"
                ? "Se reiniciarán los contenedores del sitio."
                : "Se arrancarán los contenedores del sitio."}
          </p>
          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              disabled={operateMutation.isPending}
              onClick={() => setOperateTarget(null)}
            >
              Cancelar
            </Button>
            <Button
              disabled={operateMutation.isPending}
              onClick={() => {
                if (!operateTarget) return;
                operateMutation.mutate({
                  siteId: operateTarget.site.id,
                  action: operateTarget.action,
                });
              }}
            >
              {operateMutation.isPending
                ? "Procesando..."
                : operateTarget
                  ? OPERATE_LABELS[operateTarget.action]
                  : ""}
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog open={Boolean(deleteTarget && canDelete)} title="Eliminar sitio definitivamente"
        onClose={() => setDeleteTarget(null)} zIndexClassName="z-[1100]" maxWidthClassName="max-w-md">
        <p className="text-sm">Se eliminarán los contenedores y los datos sin posibilidad de recuperación.
          Escribí {deleteTarget?.container_name} para confirmar.</p>
        <Input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
        <Button disabled={!deleteTarget || confirmation !== deleteTarget.container_name || deleteMutation.isPending}
          onClick={() => { if (deleteTarget) deleteMutation.mutate(deleteTarget.id); }}>Eliminar</Button>
      </Dialog>

      <LogsDialog site={logsTarget} onClose={() => setLogsTarget(null)} />

      <Dialog
        open={Boolean(resetTarget)}
        title="Restablecer contraseña"
        description={
          resetTarget
            ? `${resetTarget.login} (ID ${resetTarget.id}) en ${selectedSite?.container_name ?? ""}`
            : undefined
        }
        onClose={() => setResetTarget(null)}
        zIndexClassName="z-[1200]"
        maxWidthClassName="max-w-md"
      >
        <div className="space-y-3 text-sm text-foreground">
          <p>
            Se generará una contraseña nueva y se mostrará una sola vez. El usuario
            perderá la contraseña anterior.
          </p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={() => setResetTarget(null)}>
              Cancelar
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
              {resetMutation.isPending ? "..." : "Restablecer"}
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog
        open={Boolean(generated)}
        title="Contraseña generada"
        description={generated ? `Usuario ${generated.login}` : undefined}
        onClose={() => setGenerated(null)}
        zIndexClassName="z-[1300]"
        maxWidthClassName="max-w-md"
      >
        <div className="space-y-3">
          <Alert title="Se muestra una sola vez">
            Copiá esta contraseña ahora. Al cerrar este cuadro no se puede volver a
            consultar.
          </Alert>
          <div className="flex items-center gap-2">
            <code className="flex-1 overflow-x-auto rounded-md border border-border bg-muted px-3 py-2 font-mono text-sm">
              {generated?.password ?? ""}
            </code>
            <Button type="button" size="sm" onClick={() => void copyPassword()}>
              Copiar
            </Button>
          </div>
          <div className="flex justify-end">
            <Button type="button" variant="secondary" size="sm" onClick={() => setGenerated(null)}>
              Cerrar
            </Button>
          </div>
        </div>
      </Dialog>
    </section>
  );
}
