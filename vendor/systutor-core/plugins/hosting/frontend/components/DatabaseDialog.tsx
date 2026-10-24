import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { toast } from "@systutor/shell/ui/toast";
import { useState } from "react";

import { createPhpMyAdminSession, type HostingSiteDatabase } from "../api";

type DatabaseDialogProps = {
  access: HostingSiteDatabase | null;
  onClose: () => void;
};

async function copy(label: string, value: string) {
  try {
    await navigator.clipboard.writeText(value);
    toast.success(`${label} copiado`);
  } catch {
    toast.error("No se pudo copiar al portapapeles");
  }
}

/**
 * The site's own database credential. It is the user WordPress already uses,
 * limited to this site's database; the panel never creates a second one. The
 * password only lives in this component's props and disappears on close.
 */
export function DatabaseDialog({ access, onClose }: DatabaseDialogProps) {
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function close() {
    setError(null);
    onClose();
  }

  async function openPhpMyAdmin() {
    if (!access || opening) return;
    // Opening before awaiting the Bearer-authenticated POST avoids popup blockers.
    const tab = window.open("about:blank", "_blank");
    if (!tab) {
      setError("Permití las ventanas emergentes para abrir phpMyAdmin.");
      return;
    }
    tab.opener = null;
    setError(null);
    setOpening(true);
    try {
      const session = await createPhpMyAdminSession(access.site_id);
      if (!tab.closed) tab.location.replace(new URL(session.url, window.location.origin).href);
    } catch (reason) {
      tab.close();
      setError(reason instanceof Error ? reason.message : "No se pudo abrir phpMyAdmin.");
    } finally {
      setOpening(false);
    }
  }

  return (
    <Dialog
      open={Boolean(access)}
      title={`Base de datos de ${access?.container_name ?? ""}`}
      description="Credencial propia del sitio, con acceso solo a su base de datos."
      onClose={close}
      zIndexClassName="z-[1100]"
      maxWidthClassName="max-w-xl"
      actions={
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" onClick={close}>
            Cerrar
          </Button>
          <Button onClick={openPhpMyAdmin} disabled={opening}>
            {opening ? "Conectando..." : "Abrir phpMyAdmin"}
          </Button>
        </div>
      }
    >
      {access ? (
        <div className="space-y-4">
          {error ? <Alert title="No se pudo abrir phpMyAdmin">{error}</Alert> : null}
          <dl className="grid gap-3 text-sm">
            <div className="grid grid-cols-[8rem_1fr] items-center gap-2">
              <dt className="text-muted-foreground">Servidor</dt>
              <dd className="flex items-center gap-2">
                <span className="break-all font-mono text-xs text-foreground">{access.host}</span>
                <Button variant="secondary" onClick={() => copy("Servidor", access.host)}>
                  Copiar
                </Button>
              </dd>
            </div>
            <div className="grid grid-cols-[8rem_1fr] items-center gap-2">
              <dt className="text-muted-foreground">Base de datos</dt>
              <dd className="flex items-center gap-2">
                <span className="break-all font-mono text-xs text-foreground">
                  {access.database}
                </span>
                <Button variant="secondary" onClick={() => copy("Base de datos", access.database)}>
                  Copiar
                </Button>
              </dd>
            </div>
            <div className="grid grid-cols-[8rem_1fr] items-center gap-2">
              <dt className="text-muted-foreground">Usuario</dt>
              <dd className="flex items-center gap-2">
                <span className="break-all font-mono text-xs text-foreground">{access.user}</span>
                <Button variant="secondary" onClick={() => copy("Usuario", access.user)}>
                  Copiar
                </Button>
              </dd>
            </div>
            <div className="grid grid-cols-[8rem_1fr] items-center gap-2">
              <dt className="text-muted-foreground">Contraseña</dt>
              <dd className="flex items-center gap-2">
                <span className="break-all font-mono text-xs text-foreground">
                  {access.password}
                </span>
                <Button variant="secondary" onClick={() => copy("Contraseña", access.password)}>
                  Copiar
                </Button>
              </dd>
            </div>
          </dl>
        </div>
      ) : null}
    </Dialog>
  );
}
