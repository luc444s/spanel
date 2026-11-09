import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { toast } from "@systutor/shell/ui/toast";

import { appsKeys, disconnectGithub, getGithubConnection, startGithubOAuth } from "../api";
import { validateGithubAuthorizationUrl } from "../logic";

export function GithubConnectionCard({ canConnect }: { canConnect: boolean }) {
  const queryClient = useQueryClient();
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [pending, setPending] = useState(false);
  const connection = useQuery({
    queryKey: appsKeys.connection,
    queryFn: getGithubConnection,
    enabled: canConnect,
    retry: false,
  });

  async function connect() {
    setPending(true);
    try {
      const { authorization_url } = await startGithubOAuth();
      window.location.assign(validateGithubAuthorizationUrl(authorization_url));
    } catch (error) {
      setPending(false);
      toast.error(error instanceof Error ? error.message : "No se pudo iniciar GitHub OAuth");
    }
  }

  async function disconnect() {
    setConfirmDisconnect(false);
    setPending(true);
    try {
      await disconnectGithub();
      await queryClient.invalidateQueries({ queryKey: appsKeys.connection });
      toast.success("GitHub desconectado");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo desconectar GitHub");
    } finally {
      setPending(false);
    }
  }

  if (!canConnect) return null;
  const data = connection.data;
  return (
    <div className="rounded-md border border-border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          {data?.avatar_url ? <img className="h-10 w-10 rounded-full" src={data.avatar_url} alt="" /> : null}
          <div>
            <p className="font-medium text-foreground">GitHub</p>
            {data?.connected ? (
              <p className="text-sm text-muted-foreground">
                @{data.login} · {(data.scopes ?? []).join(", ") || "sin scopes informados"}
              </p>
            ) : (
              <p className="text-sm text-muted-foreground">
                {data?.reconnect_required ? "La conexión caducó o fue revocada" : "Sin conexión"}
              </p>
            )}
          </div>
        </div>
        <div className="flex gap-2">
          {data?.connected ? (
            <Button variant="secondary" disabled={pending} onClick={() => setConfirmDisconnect(true)}>
              Desconectar
            </Button>
          ) : (
            <Button disabled={pending || connection.isLoading} onClick={() => void connect()}>
              {data?.reconnect_required ? "Reconectar GitHub" : "Conectar GitHub"}
            </Button>
          )}
        </div>
      </div>
      {connection.error ? <Alert title="GitHub">No se pudo leer el estado de la conexión.</Alert> : null}
      <Dialog open={confirmDisconnect} title="Desconectar GitHub" onClose={() => setConfirmDisconnect(false)} maxWidthClassName="max-w-md">
        <div className="space-y-4 text-sm">
          <p>Las nuevas inspecciones y despliegues quedarán bloqueados. El lifecycle existente seguirá disponible.</p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setConfirmDisconnect(false)}>Cancelar</Button>
            <Button disabled={pending} onClick={() => void disconnect()}>Desconectar</Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
