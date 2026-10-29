import { useEffect, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";

import {
  certificateHostnameError,
  normalizeCertificateHostname,
} from "./certificateHostname";

/**
 * Alta del certificado TLS de un dominio nuevo (A.SPEC 0041).
 *
 * Pide un hostname `mail.<dominio>` y encola un `ensure` aunque el
 * certificado todavía no aparezca en el inventario del agente. Formulario
 * sin éxito propio: el hook cierra el diálogo cuando el job queda encolado.
 */
export function NewCertificateDialog({
  open,
  pending,
  error,
  onSubmit,
  onClose,
}: {
  open: boolean;
  pending: boolean;
  error: string | null;
  onSubmit: (hostname: string) => void;
  onClose: () => void;
}) {
  const [hostname, setHostname] = useState("");

  useEffect(() => {
    if (open) setHostname("");
  }, [open]);

  const validationError = certificateHostnameError(hostname);
  const normalized = normalizeCertificateHostname(hostname);

  function submit() {
    if (validationError || pending) return;
    onSubmit(normalized);
  }

  return (
    <Dialog
      open={open}
      title="Agregar certificado"
      description="Solicitud de emisión para un dominio nuevo"
      onClose={onClose}
      zIndexClassName="z-[1250]"
      maxWidthClassName="max-w-md"
    >
      <div className="space-y-4 text-sm text-foreground">
        <div className="space-y-1">
          <label htmlFor="new-certificate-hostname" className="text-xs text-muted-foreground">
            Hostname del certificado
          </label>
          <Input
            id="new-certificate-hostname"
            value={hostname}
            autoComplete="off"
            spellCheck={false}
            placeholder="mail.nuevodominio.com"
            disabled={pending}
            onChange={(event) => setHostname(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") submit();
            }}
          />
          {hostname && validationError ? (
            <p className="text-xs text-destructive">{validationError}</p>
          ) : null}
        </div>
        <Alert title="Antes de emitir">
          El hostname debe estar en la allowlist del agente
          (<span className="font-mono text-xs">/etc/spanel/cert-agent.toml</span>).
          Si es un dominio recién agregado, ejecutá primero
          <span className="font-mono text-xs">
            {" "}
            python3 scripts/install_cert_agent.py --add-allowlist {normalized || "mail.<dominio>"}
          </span>{" "}
          y refrescá el estado; sin allowlist el agente rechaza la emisión.
        </Alert>
        {error ? <Alert title="No se pudo encolar la solicitud">{error}</Alert> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose} disabled={pending}>
            Cancelar
          </Button>
          <Button
            type="button"
            onClick={submit}
            disabled={pending || Boolean(validationError)}
          >
            {pending ? "Solicitando..." : "Solicitar certificado"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
