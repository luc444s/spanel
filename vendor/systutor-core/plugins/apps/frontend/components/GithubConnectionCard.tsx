import type { UseQueryResult } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { toast } from "@systutor/shell/ui/toast";

import { appsKeys, disconnectGithub, startGithubOAuth } from "../api";
import {
  githubCardContent, githubCardState, validateGithubAuthorizationUrl,
} from "../logic";
import type { GithubStatus } from "../types";

type Props = {
  canConnect: boolean;
  statusQuery: UseQueryResult<GithubStatus, Error>;
};

export function GithubConnectionCard({ canConnect, statusQuery }: Props) {
  const queryClient = useQueryClient();
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [pending, setPending] = useState(false);
  const state = githubCardState({
    loading: statusQuery.isLoading,
    error: statusQuery.isError,
    registering: pending,
    status: statusQuery.data,
  });
  const content = githubCardContent(state);
  const data = statusQuery.data;

  async function connect() {
    setPending(true);
    try {
      const { authorization_url } = await startGithubOAuth();
      window.location.assign(validateGithubAuthorizationUrl(authorization_url));
    } catch (error) {
      setPending(false);
      toast.error(error instanceof Error ? error.message : "No se pudo iniciar GitHub");
    }
  }

  async function disconnect() {
    setConfirmDisconnect(false);
    setPending(true);
    try {
      await disconnectGithub();
      await queryClient.invalidateQueries({ queryKey: appsKeys.status });
      toast.success("GitHub desconectado");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo desconectar GitHub");
    } finally {
      setPending(false);
    }
  }

  if (!canConnect) return null;
  return (
    <div className="rounded-md border border-border bg-card p-4">
      {content.label ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="font-medium text-foreground">GitHub</p>
            <p className="text-sm text-muted-foreground">
              {content.label}
              {state === "connected" && data?.login ? ` · @${data.login}` : ""}
            </p>
          </div>
          <div className="flex gap-2">
            {state === "connected" ? (
              <Button variant="secondary" disabled={pending} onClick={() => setConfirmDisconnect(true)}>
                Desconectar
              </Button>
            ) : null}
            {state === "unconfigured" ? (
              <Button disabled={pending} onClick={() => void connect()}>Conectar GitHub</Button>
            ) : null}
          </div>
        </div>
      ) : null}
      {content.alert ? (
        <Alert title="GitHub">{content.alert}</Alert>
      ) : null}
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
