import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import {
  hostingKeys,
  createHostingSite,
  operateHostingSite,
  hostingSiteLogs,
  deleteHostingSite,
  listHostingSites,
  listSiteUsers,
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

type GeneratedPassword = {
  login: string;
  password: string;
};

function stateLabel(status: string) {
  if (status === "running") return "En marcha";
  if (status === "missing") return "No existe";
  return status;
}

export default function HostingPage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((state) => state.permissions);
  const canCreate = permissions.includes("hosting.sites.create");
  const canManage = permissions.includes("hosting.sites.manage");
  const canDelete = permissions.includes("hosting.sites.delete");
  const canReadUsers = permissions.includes("hosting.users.read");
  const canReset = permissions.includes("hosting.users.password");
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [email, setEmail] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<HostingSite | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [logsTarget, setLogsTarget] = useState<HostingSite | null>(null);
  const [selectedSite, setSelectedSite] = useState<HostingSite | null>(null);
  const [resetTarget, setResetTarget] = useState<WordPressUser | null>(null);
  const [generated, setGenerated] = useState<GeneratedPassword | null>(null);

  const sitesQuery = useQuery({
    queryKey: hostingKeys.sites,
    queryFn: listHostingSites,
  });

  const usersQuery = useQuery({
    queryKey: hostingKeys.users(selectedSite?.id ?? ""),
    queryFn: () => listSiteUsers(selectedSite!.id),
    enabled: Boolean(selectedSite && canReadUsers),
  });

  const logsQuery = useQuery({
    queryKey: ["hosting", logsTarget?.id, "logs"],
    queryFn: () => hostingSiteLogs(logsTarget!.id),
    enabled: Boolean(logsTarget),
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
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: hostingKeys.sites }); },
    onError: (error: Error) => toast.error(error.message),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteHostingSite,
    onSuccess: async () => {
      setDeleteTarget(null); setConfirmation(""); setSelectedSite(null); setLogsTarget(null);
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
  const users = usersQuery.data?.users ?? [];

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
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="pb-2 pr-4">Sitio</th>
                <th className="pb-2 pr-4">Dominio</th>
                <th className="pb-2 pr-4">Estado</th>
                <th className="pb-2 pr-4">Gestionable</th>
                <th className="pb-2">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {sites.map((site) => (
                <tr key={site.id} className="border-b border-border/50">
                  <td className="py-2 pr-4 font-mono text-xs">{site.container_name}</td>
                  <td className="py-2 pr-4">{site.domain ?? "—"}</td>
                  <td className="py-2 pr-4">{stateLabel(site.status)}</td>
                  <td className="py-2 pr-4">{site.managed ? "Sí" : "No"}</td>
                  <td className="py-2">
                    {canReadUsers ? <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => setSelectedSite(site)}
                    >
                      Ver usuarios
                    </Button> : null}
                    <Button size="sm" variant="secondary" onClick={() => setLogsTarget(site)}>
                      Logs
                    </Button>
                    {canManage ? (["start", "stop", "restart"] as const).map((action) => (
                      <Button key={action} size="sm" variant="secondary"
                        disabled={!site.managed || operateMutation.isPending}
                        onClick={() => operateMutation.mutate({ siteId: site.id, action })}>
                        {{ start: "Iniciar", stop: "Detener", restart: "Reiniciar" }[action]}
                      </Button>
                    )) : null}
                    {canDelete ? <Button size="sm" variant="secondary" disabled={!site.managed}
                      onClick={() => { setDeleteTarget(site); setConfirmation(""); }}>Eliminar</Button> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selectedSite ? (
        <div className="space-y-3 rounded-md border border-border bg-card p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-foreground">
              Usuarios de {selectedSite.container_name}
            </h2>
            {!selectedSite.managed ? (
              <span className="text-xs text-muted-foreground">
                Sitio no gestionable: solo lectura
              </span>
            ) : null}
          </div>

          {usersQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">Cargando usuarios...</p>
          ) : usersQuery.error ? (
            <Alert title="Error">No se pudieron cargar los usuarios del sitio.</Alert>
          ) : users.length === 0 ? (
            <p className="text-sm text-muted-foreground">El sitio no tiene usuarios.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th className="pb-2 pr-4">ID</th>
                    <th className="pb-2 pr-4">Usuario</th>
                    <th className="pb-2 pr-4">Email</th>
                    <th className="pb-2 pr-4">Estado</th>
                    <th className="pb-2 pr-4">Rol</th>
                    {selectedSite.managed && canReset ? <th className="pb-2">Acciones</th> : null}
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => (
                    <tr key={user.id} className="border-b border-border/50">
                      <td className="py-2 pr-4 font-mono text-xs">{user.id}</td>
                      <td className="py-2 pr-4">{user.login}</td>
                      <td className="py-2 pr-4">{user.email}</td>
                      <td className="py-2 pr-4">
                        {user.status === 0 ? "Activo" : "Inactivo"}
                      </td>
                      <td className="py-2 pr-4">{user.role}</td>
                      {selectedSite.managed && canReset ? (
                        <td className="py-2">
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => setResetTarget(user)}
                          >
                            Restablecer
                          </Button>
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : null}

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

      <Dialog open={Boolean(deleteTarget && canDelete)} title="Eliminar sitio definitivamente"
        onClose={() => setDeleteTarget(null)}>
        <p className="text-sm">Se eliminarán los contenedores y los datos sin posibilidad de recuperación.
          Escribí {deleteTarget?.container_name} para confirmar.</p>
        <Input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
        <Button disabled={!deleteTarget || confirmation !== deleteTarget.container_name || deleteMutation.isPending}
          onClick={() => { if (deleteTarget) deleteMutation.mutate(deleteTarget.id); }}>Eliminar</Button>
      </Dialog>

      <Dialog open={Boolean(logsTarget)} title={`Logs de ${logsTarget?.container_name ?? ""}`}
        onClose={() => setLogsTarget(null)}>
        {logsQuery.error ? <Alert title="Error">No se pudieron leer los logs.</Alert> :
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs">{logsQuery.data?.logs ?? "Cargando..."}</pre>}
      </Dialog>

      <Dialog
        open={Boolean(resetTarget)}
        title="Restablecer contraseña"
        description={
          resetTarget
            ? `${resetTarget.login} (ID ${resetTarget.id}) en ${selectedSite?.container_name ?? ""}`
            : undefined
        }
        onClose={() => setResetTarget(null)}
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
