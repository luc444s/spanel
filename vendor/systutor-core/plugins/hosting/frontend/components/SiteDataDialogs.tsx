import { useQuery } from "@tanstack/react-query";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";

import { hostingKeys, hostingSiteLogs, listSiteUsers, type HostingSite, type WordPressUser } from "../api";

type UsersDialogProps = {
  site: HostingSite | null;
  canReset: boolean;
  onClose: () => void;
  onReset: (user: WordPressUser) => void;
};

/**
 * Users live in their own dialog on top of the site dialog. The query is
 * enabled by the open site, so closing the dialog also stops asking Docker.
 */
export function UsersDialog({ site, canReset, onClose, onReset }: UsersDialogProps) {
  const usersQuery = useQuery({
    queryKey: hostingKeys.users(site?.id ?? ""),
    queryFn: () => listSiteUsers(site!.id),
    enabled: Boolean(site),
  });
  const users = usersQuery.data?.users ?? [];

  return (
    <Dialog
      open={Boolean(site)}
      title={`Usuarios de ${site?.container_name ?? ""}`}
      description={site && !site.managed ? "Sitio no gestionable: solo lectura" : undefined}
      onClose={onClose}
      zIndexClassName="z-[1100]"
      maxWidthClassName="max-w-3xl"
    >
      {usersQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Cargando usuarios...</p>
      ) : usersQuery.error ? (
        <Alert title="No se pudieron cargar los usuarios">
          {usersQuery.error instanceof Error
            ? usersQuery.error.message
            : "Error desconocido al consultar el sitio."}
        </Alert>
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
                {site?.managed && canReset ? <th className="pb-2">Acciones</th> : null}
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id} className="border-b border-border/50">
                  <td className="py-2 pr-4 font-mono text-xs">{user.id}</td>
                  <td className="py-2 pr-4">{user.login}</td>
                  <td className="py-2 pr-4">{user.email}</td>
                  <td className="py-2 pr-4">{user.status === 0 ? "Activo" : "Inactivo"}</td>
                  <td className="py-2 pr-4">{user.role}</td>
                  {site?.managed && canReset ? (
                    <td className="py-2">
                      <Button size="sm" variant="secondary" onClick={() => onReset(user)}>
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
    </Dialog>
  );
}

type LogsDialogProps = {
  site: HostingSite | null;
  onClose: () => void;
};

export function LogsDialog({ site, onClose }: LogsDialogProps) {
  const logsQuery = useQuery({
    queryKey: ["hosting", site?.id, "logs"],
    queryFn: () => hostingSiteLogs(site!.id),
    enabled: Boolean(site),
  });

  return (
    <Dialog
      open={Boolean(site)}
      title={`Logs de ${site?.container_name ?? ""}`}
      onClose={onClose}
      zIndexClassName="z-[1100]"
      maxWidthClassName="max-w-3xl"
    >
      {logsQuery.error ? (
        <Alert title="No se pudieron leer los logs">
          {logsQuery.error instanceof Error
            ? logsQuery.error.message
            : "Error desconocido al consultar el sitio."}
        </Alert>
      ) : (
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs">
          {logsQuery.data?.logs ?? "Cargando..."}
        </pre>
      )}
    </Dialog>
  );
}
