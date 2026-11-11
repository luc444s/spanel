import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useEffect, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { Select } from "@systutor/shell/ui/select";
import { toast } from "@systutor/shell/ui/toast";
import { useT } from "@/i18n/use-i18n";

import {
  appsKeys, listInstallations, listInstallationRepositories, updateApplicationSource,
} from "../api";
import type { Application } from "../types";

export function SourceDialog({ application, open, onClose }: {
  application: Application;
  open: boolean;
  onClose: () => void;
}) {
  const t = useT();
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
    if (!installationId || !repositoryId) return setError(t("Choose an authorized installation and repository"));
    setPending(true); setError(null);
    try {
      await updateApplicationSource(application.id, installationId, repositoryId, branch || null);
      await queryClient.invalidateQueries({ queryKey: appsKeys.detail(application.id) });
      await queryClient.invalidateQueries({ queryKey: appsKeys.applications });
      toast.success(t("Authorized source updated"));
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("Could not update the source"));
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} title={t("Select authorized source")} onClose={onClose}>
      <form className="space-y-3 text-sm" onSubmit={(event) => void submit(event)}>
        <label className="block space-y-1">
          <span>{t("GitHub installation")}</span>
          <Select
            value={installationId}
            onChange={(value) => { setInstallationId(value); setRepositoryId(""); }}
            options={(installations.data ?? []).filter((item) => item.status === "active").map((item) => ({
              value: item.id,
              label: t("@{account} · {count} repos", {
                account: item.account_login, count: item.repository_count,
              }),
            }))}
            placeholder={t("Choose installation")}
          />
        </label>
        <label className="block space-y-1">
          <span>{t("Authorized repository")}</span>
          <Select
            value={repositoryId}
            onChange={setRepositoryId}
            options={(repositories.data ?? []).map((item) => ({
              value: item.id, label: item.full_name,
            }))}
            placeholder={t("Choose repository")}
          />
        </label>
        <label className="block space-y-1"><span>{t("Branch")}</span><Input value={branch} onChange={(e) => setBranch(e.target.value)} /></label>
        {error ? <Alert title={t("Error")}>{error}</Alert> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" disabled={pending} onClick={onClose}>{t("Cancel")}</Button>
          <Button type="submit" disabled={pending || !installationId || !repositoryId}>{t("Save source")}</Button>
        </div>
      </form>
    </Dialog>
  );
}
