import type { UseQueryResult } from "@tanstack/react-query";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { toast } from "@systutor/shell/ui/toast";

import {
  appsKeys, deleteProvider, listInstallations, listProviders, newIdempotencyKey,
  startGithubManifest, syncInstallation,
} from "../api";
import {
  githubCardContent, githubCardState, validateGithubInstallUrl,
  validateGithubManifestUrl, validateGithubOrganization,
} from "../logic";
import type { GithubStatus } from "../types";

type Props = {
  canConnect: boolean;
  statusQuery: UseQueryResult<GithubStatus, Error>;
};

export function GithubConnectionCard({ canConnect, statusQuery }: Props) {
  const queryClient = useQueryClient();
  const [connectOpen, setConnectOpen] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [ownerType, setOwnerType] = useState<"personal" | "organization">("personal");
  const [organization, setOrganization] = useState("");
  const [pending, setPending] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const state = githubCardState({
    loading: statusQuery.isLoading,
    error: statusQuery.isError,
    registering: pending,
    status: statusQuery.data,
  });
  const content = githubCardContent(state);
  const data = statusQuery.data;
  const canManage = ["connected", "syncing", "suspended", "pending_installation"].includes(state);
  const installations = useQuery({
    queryKey: appsKeys.installations, queryFn: listInstallations,
    enabled: canConnect && canManage, retry: false,
  });
  const providers = useQuery({
    queryKey: appsKeys.providers, queryFn: listProviders,
    enabled: canConnect && canManage, retry: false,
  });
  const activeInstallation = installations.data?.find((item) => item.status === "active");

  async function startManifest(event: FormEvent) {
    event.preventDefault();
    setDialogError(null);
    setPending(true);
    try {
      const target = ownerType === "organization"
        ? validateGithubOrganization(organization) : null;
      const started = await startGithubManifest(ownerType, target);
      const actionUrl = validateGithubManifestUrl(started.action_url, target);
      const form = document.createElement("form");
      form.method = "post";
      form.action = actionUrl;
      form.style.display = "none";
      for (const [name, value] of [
        ["manifest", JSON.stringify(started.manifest)],
        ["state", started.state],
      ] as const) {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = name;
        input.value = value;
        form.appendChild(input);
      }
      document.body.appendChild(form);
      form.submit();
    } catch (error) {
      setPending(false);
      setDialogError(error instanceof Error ? error.message : "No se pudo registrar GitHub App");
    }
  }

  function openInstall() {
    const url = data?.install_url;
    if (!url) return;
    try {
      window.location.assign(validateGithubInstallUrl(url));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "URL de instalación inválida");
    }
  }

  async function sync() {
    if (!activeInstallation) return;
    setPending(true);
    try {
      await syncInstallation(activeInstallation.id, newIdempotencyKey());
      await queryClient.invalidateQueries({ queryKey: appsKeys.status });
      await queryClient.invalidateQueries({ queryKey: appsKeys.installations });
      toast.success("Sincronización encolada");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo sincronizar");
    } finally {
      setPending(false);
    }
  }

  async function disconnect() {
    setConfirmDisconnect(false);
    setPending(true);
    try {
      const rows = providers.data ?? [];
      await Promise.all(rows.filter((row) => row.status !== "revoked").map((row) => deleteProvider(row.id)));
      await queryClient.invalidateQueries({ queryKey: appsKeys.status });
      await queryClient.invalidateQueries({ queryKey: appsKeys.providers });
      await queryClient.invalidateQueries({ queryKey: appsKeys.installations });
      toast.success("GitHub App retirada");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo retirar GitHub App");
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
              {state === "connected" && activeInstallation
                ? ` · ${activeInstallation.repository_count} repositorios` : ""}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {state === "unconfigured" ? (
              <Button disabled={pending} onClick={() => setConnectOpen(true)}>Conectar GitHub</Button>
            ) : null}
            {state === "pending_installation" && data?.install_url ? (
              <Button disabled={pending} onClick={openInstall}>Instalar GitHub App</Button>
            ) : null}
            {state === "connected" && activeInstallation ? (
              <Button variant="secondary" disabled={pending} onClick={() => void sync()}>
                Sincronizar
              </Button>
            ) : null}
            {["connected", "syncing", "suspended"].includes(state) ? (
              <Button variant="secondary" disabled={pending} onClick={() => setConfirmDisconnect(true)}>
                Retirar GitHub App
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}
      {content.alert ? <Alert title="GitHub">{content.alert}</Alert> : null}

      <Dialog open={connectOpen} title="Conectar GitHub" onClose={() => setConnectOpen(false)} maxWidthClassName="max-w-md">
        <form className="space-y-4 text-sm" onSubmit={(event) => void startManifest(event)}>
          <p>SPanel registrará una GitHub App propia de esta instalación. Elegí dónde instalarla.</p>
          <label className="flex items-center gap-2">
            <input type="radio" checked={ownerType === "personal"} onChange={() => setOwnerType("personal")} />
            Cuenta personal
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" checked={ownerType === "organization"} onChange={() => setOwnerType("organization")} />
            Organización
          </label>
          {ownerType === "organization" ? (
            <label className="block space-y-1">
              <span>Slug de organización</span>
              <Input required value={organization} onChange={(event) => setOrganization(event.target.value)} placeholder="acme" />
            </label>
          ) : null}
          {dialogError ? <Alert title="Error">{dialogError}</Alert> : null}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" disabled={pending} onClick={() => setConnectOpen(false)}>Cancelar</Button>
            <Button type="submit" disabled={pending}>{pending ? "Registrando…" : "Registrar e instalar"}</Button>
          </div>
        </form>
      </Dialog>

      <Dialog open={confirmDisconnect} title="Retirar GitHub App" onClose={() => setConfirmDisconnect(false)} maxWidthClassName="max-w-md">
        <div className="space-y-4 text-sm">
          <p>Se revocan las credenciales del tenant. Las aplicaciones que las usaban quedan en source reconnect required y el lifecycle existente sigue disponible.</p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setConfirmDisconnect(false)}>Cancelar</Button>
            <Button disabled={pending} onClick={() => void disconnect()}>Retirar</Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
