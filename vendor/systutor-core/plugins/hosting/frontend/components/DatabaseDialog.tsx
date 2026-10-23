import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { toast } from "@systutor/shell/ui/toast";

import type { HostingSiteDatabase } from "../api";

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
  function openPhpMyAdmin() {
    if (!access?.url) return;
    window.open(access.url, "_blank", "noopener,noreferrer");
  }

  return (
    <Dialog
      open={Boolean(access)}
      title={`Base de datos de ${access?.container_name ?? ""}`}
      description="Credencial propia del sitio, con acceso solo a su base de datos."
      onClose={onClose}
      zIndexClassName="z-[1100]"
      maxWidthClassName="max-w-xl"
      actions={
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cerrar
          </Button>
          <Button onClick={openPhpMyAdmin} disabled={!access?.url}>
            Abrir phpMyAdmin
          </Button>
        </div>
      }
    >
      {access ? (
        <div className="space-y-4">
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

          {access.url ? (
            <p className="text-xs text-muted-foreground">
              phpMyAdmin abre en una pestaña nueva. Pega el usuario y la contraseña allí: el
              panel no los envía en la URL.
            </p>
          ) : (
            <Alert title="phpMyAdmin no está configurado">
              Define SPANEL_PHPMYADMIN_URL para que este botón pueda abrir la consola. La
              credencial de arriba funciona igual.
            </Alert>
          )}
        </div>
      ) : null}
    </Dialog>
  );
}
