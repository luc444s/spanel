import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { toast } from "@systutor/shell/ui/toast";

import { useT } from "@/i18n/use-i18n";

import {
  exportHostingBackup,
  hostingKeys,
  importHostingBackup,
  type HostingSite,
} from "../api";

type BackupDialogsProps = {
  site: HostingSite;
};

type ExportLink = {
  url: string;
  filename: string;
  expiresIn: number;
};

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

/** Backup actions and their overlay dialogs for one owned, manageable site. */
export function BackupDialogs({ site }: BackupDialogsProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [exportOpen, setExportOpen] = useState(false);
  const [exportPending, setExportPending] = useState(false);
  const [exportLink, setExportLink] = useState<ExportLink | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const exportGeneration = useRef(0);
  const [importOpen, setImportOpen] = useState(false);
  const [importPending, setImportPending] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [confirmation, setConfirmation] = useState("");

  const running = site.status === "running";
  const validFile = Boolean(file?.name.toLowerCase().endsWith(".zip"));
  const canImport = validFile && confirmation === site.container_name && !importPending;
  const filesExtra = site.kind === "wordpress" ? t(" and the database") : "";

  function closeExport() {
    exportGeneration.current += 1;
    setExportOpen(false);
    setExportLink(null);
    setExportError(null);
  }

  async function generateExport() {
    if (exportPending) return;
    const generation = ++exportGeneration.current;
    setExportPending(true);
    setExportLink(null);
    setExportError(null);
    try {
      const result = await exportHostingBackup(site.id);
      if (generation !== exportGeneration.current) return;
      setExportLink({
        url: new URL(result.url, window.location.origin).href,
        filename: result.filename,
        expiresIn: result.expires_in,
      });
    } catch (error) {
      if (generation === exportGeneration.current) {
        setExportError(errorMessage(error, t("Could not generate the backup.")));
      }
    } finally {
      setExportPending(false);
    }
  }

  async function copyExportLink() {
    if (!exportLink) return;
    try {
      await navigator.clipboard.writeText(exportLink.url);
      toast.success(t("Link copied"));
    } catch {
      toast.error(t("Could not copy the link"));
    }
  }

  function closeImport() {
    setImportOpen(false);
    setFile(null);
    setConfirmation("");
  }

  function submitImport() {
    if (!file || !canImport) return;
    const selectedFile = file;
    const exactConfirmation = confirmation;
    closeImport();
    setImportPending(true);
    const task = importHostingBackup(site.id, selectedFile, exactConfirmation)
      .then(async () => {
        await queryClient.invalidateQueries({ queryKey: hostingKeys.sites });
      })
      .finally(() => setImportPending(false));
    toast.promise(task, {
      loading: t("Restoring backup…"),
      success: t("Backup restored"),
      error: (error: Error) => error.message || t("Could not restore the backup"),
    });
    void task.catch(() => undefined);
  }

  return (
    <>
      <Button
        type="button"
        variant="secondary"
        disabled={!running || importPending}
        onClick={() => setImportOpen(true)}
      >
        {t("Import")}
      </Button>
      <Button
        type="button"
        variant="secondary"
        disabled={!running || exportPending}
        onClick={() => setExportOpen(true)}
      >
        {t("Export")}
      </Button>

      <Dialog
        open={exportOpen}
        title={t("Export backup of {site}", { site: site.container_name })}
        description={site.domain ?? t("No domain")}
        onClose={closeExport}
        zIndexClassName="z-[1100]"
        maxWidthClassName="max-w-xl"
      >
        <div className="space-y-4">
          <Alert title={t("Temporary single-download link")}>
            {t("The ZIP includes the files{extra}. Anyone with the link can download it. The link expires, works only once, and a preview or bot may consume it before you.", { extra: filesExtra })}
          </Alert>
          {exportError ? <Alert title={t("Could not export")}>{exportError}</Alert> : null}
          {exportLink ? (
            <div className="space-y-3">
              <p className="text-sm text-foreground">
                {t("{filename} · expires in {seconds} seconds", {
                  filename: exportLink.filename,
                  seconds: exportLink.expiresIn,
                })}
              </p>
              <code className="block overflow-x-auto rounded-md border border-border bg-muted px-3 py-2 font-mono text-xs text-foreground">
                {exportLink.url}
              </code>
              <div className="flex flex-wrap justify-end gap-2">
                <Button type="button" variant="secondary" onClick={() => void copyExportLink()}>
                  {t("Copy link")}
                </Button>
                <Button
                  type="button"
                  onClick={() => window.open(exportLink.url, "_blank", "noopener,noreferrer")}
                >
                  {t("Open/download")}
                </Button>
              </div>
            </div>
          ) : null}
          <div className="flex flex-wrap justify-end gap-2">
            <Button type="button" variant="secondary" onClick={closeExport}>
              {t("Close")}
            </Button>
            <Button type="button" disabled={exportPending} onClick={() => void generateExport()}>
              {exportPending ? t("Generating backup…") : t("Generate temporary link")}
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog
        open={importOpen}
        title={t("Import backup into {site}", { site: site.container_name })}
        description={site.domain ?? t("No domain")}
        onClose={closeImport}
        zIndexClassName="z-[1100]"
        maxWidthClassName="max-w-xl"
      >
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            submitImport();
          }}
        >
          <Alert title={t("This operation replaces the site")}>
            {t("The files{extra} will be replaced. The system creates a safety backup and attempts a rollback if something fails. Do not interrupt the operation once it has started.", { extra: filesExtra })}
          </Alert>
          <label className="block space-y-1 text-sm text-foreground">
            <span>{t("Backup file")}</span>
            <Input
              type="file"
              accept=".zip,application/zip"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>
              {t("Type {name} to confirm", { name: site.container_name })}
            </span>
            <Input
              value={confirmation}
              autoComplete="off"
              onChange={(event) => setConfirmation(event.target.value)}
            />
          </label>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={closeImport}>
              {t("Cancel")}
            </Button>
            <Button type="submit" disabled={!canImport}>
              {t("Import")}
            </Button>
          </div>
        </form>
      </Dialog>
    </>
  );
}
