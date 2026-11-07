import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { toast } from "@systutor/shell/ui/toast";

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
        setExportError(errorMessage(error, "No se pudo generar el respaldo."));
      }
    } finally {
      setExportPending(false);
    }
  }

  async function copyExportLink() {
    if (!exportLink) return;
    try {
      await navigator.clipboard.writeText(exportLink.url);
      toast.success("Enlace copiado");
    } catch {
      toast.error("No se pudo copiar el enlace");
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
      loading: "Restaurando respaldo…",
      success: "Respaldo restaurado",
      error: (error: Error) => error.message || "No se pudo restaurar el respaldo",
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
        Importar
      </Button>
      <Button
        type="button"
        variant="secondary"
        disabled={!running || exportPending}
        onClick={() => setExportOpen(true)}
      >
        Exportar
      </Button>

      <Dialog
        open={exportOpen}
        title={`Exportar respaldo de ${site.container_name}`}
        description={site.domain ?? "Sin dominio"}
        onClose={closeExport}
        zIndexClassName="z-[1100]"
        maxWidthClassName="max-w-xl"
      >
        <div className="space-y-4">
          <Alert title="Enlace temporal de una sola descarga">
            El ZIP incluye los archivos{site.kind === "wordpress" ? " y la base de datos" : ""}.
            Cualquiera que tenga el enlace puede descargarlo. El enlace expira, funciona una sola
            vez y una vista previa o bot puede consumirlo antes que vos.
          </Alert>
          {exportError ? <Alert title="No se pudo exportar">{exportError}</Alert> : null}
          {exportLink ? (
            <div className="space-y-3">
              <p className="text-sm text-foreground">
                <span className="font-medium">{exportLink.filename}</span> · expira en {exportLink.expiresIn}
                segundos
              </p>
              <code className="block overflow-x-auto rounded-md border border-border bg-muted px-3 py-2 font-mono text-xs text-foreground">
                {exportLink.url}
              </code>
              <div className="flex flex-wrap justify-end gap-2">
                <Button type="button" variant="secondary" onClick={() => void copyExportLink()}>
                  Copiar enlace
                </Button>
                <Button
                  type="button"
                  onClick={() => window.open(exportLink.url, "_blank", "noopener,noreferrer")}
                >
                  Abrir/descargar
                </Button>
              </div>
            </div>
          ) : null}
          <div className="flex flex-wrap justify-end gap-2">
            <Button type="button" variant="secondary" onClick={closeExport}>
              Cerrar
            </Button>
            <Button type="button" disabled={exportPending} onClick={() => void generateExport()}>
              {exportPending ? "Generando respaldo…" : "Generar enlace temporal"}
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog
        open={importOpen}
        title={`Importar respaldo en ${site.container_name}`}
        description={site.domain ?? "Sin dominio"}
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
          <Alert title="Esta operación reemplaza el sitio">
            Se reemplazarán los archivos{site.kind === "wordpress" ? " y la base de datos" : ""}.
            El sistema crea un respaldo de seguridad e intenta rollback si algo falla. No
            interrumpas la operación una vez iniciada.
          </Alert>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Archivo de respaldo</span>
            <Input
              type="file"
              accept=".zip,application/zip"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>
              Escribí <strong className="font-mono">{site.container_name}</strong> para confirmar
            </span>
            <Input
              value={confirmation}
              autoComplete="off"
              onChange={(event) => setConfirmation(event.target.value)}
            />
          </label>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={closeImport}>
              Cancelar
            </Button>
            <Button type="submit" disabled={!canImport}>
              Importar
            </Button>
          </div>
        </form>
      </Dialog>
    </>
  );
}
