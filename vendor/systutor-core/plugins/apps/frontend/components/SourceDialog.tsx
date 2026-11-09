import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useEffect, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { Select } from "@systutor/shell/ui/select";
import { toast } from "@systutor/shell/ui/toast";

import {
  appsKeys, listInstallations, listInstallationRepositories, updateApplicationSource,
} from "../api";
import type { Application } from "../types";

export function SourceDialog({ application, open, onClose }: {
  application: Application;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [installationId, setInstallationId] = useState(application.source_installation_id ?? "");
  const [repositoryId, setRepositoryId] = useState(application.source_repository_id ?? "");
  const [branch, setBranch] = useState(application.branch);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const installations = useQuery({
    queryKey: appsKeys.installations, queryFn: listInstallations,
    enabled: open, retry: false,
  });
  const repositories = useQuery({
    queryKey: appsKeys.repositories(installationId || "idle"),
    queryFn: () => listInstallationRepositories(installationId),
    enabled: open && Boolean(installationId), retry: false,
  });
  const selected = repositories.data?.find((item) => item.id === repositoryId);

  useEffect(() => {
    if (selected) setBranch(selected.default_branch || "main");
  }, [selected]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!installationId || !repositoryId) return setError("Elegí instalación y repositorio autorizado");
    setPending(true); setError(null);
    try {
      await updateApplicationSource(application.id, installationId, repositoryId, branch || null);
      await queryClient.invalidateQueries({ queryKey: appsKeys.detail(application.id) });
      await queryClient.invalidateQueries({ queryKey: appsKeys.applications });
      toast.success("Fuente autorizada actualizada");
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo actualizar la fuente");
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} title="Seleccionar fuente autorizada" onClose={onClose}>
      <form className="space-y-3 text-sm" onSubmit={(event) => void submit(event)}>
        <label className="block space-y-1">
          <span>Instalación GitHub</span>
          <Select
            value={installationId}
            onChange={(value) => { setInstallationId(value); setRepositoryId(""); }}
            options={(installations.data ?? []).filter((item) => item.status === "active").map((item) => ({
              value: item.id, label: `@${item.account_login} · ${item.repository_count} repos`,
            }))}
            placeholder="Elegir instalación"
          />
        </label>
        <label className="block space-y-1">
          <span>Repositorio autorizado</span>
          <Select
            value={repositoryId}
            onChange={setRepositoryId}
            options={(repositories.data ?? []).map((item) => ({
              value: item.id, label: item.full_name,
            }))}
            placeholder="Elegir repositorio"
          />
        </label>
        <label className="block space-y-1"><span>Rama</span><Input value={branch} onChange={(e) => setBranch(e.target.value)} /></label>
        {error ? <Alert title="Error">{error}</Alert> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" disabled={pending} onClick={onClose}>Cancelar</Button>
          <Button type="submit" disabled={pending || !installationId || !repositoryId}>Guardar fuente</Button>
        </div>
      </form>
    </Dialog>
  );
}
