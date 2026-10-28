import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";

import type { MailCertificate } from "../../api";

export function ForceIssueDialog({
  target,
  pending,
  disabled,
  onCancel,
  onConfirm,
}: {
  target: MailCertificate | null;
  pending: boolean;
  disabled: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog
      open={Boolean(target)}
      title="Forzar reemisión"
      description={target?.hostname}
      onClose={onCancel}
      zIndexClassName="z-[1250]"
      maxWidthClassName="max-w-md"
    >
      <div className="space-y-4 text-sm text-foreground">
        <Alert title="Advertencia de rate limit">
          Let’s Encrypt limita los certificados duplicados por dominio. Forzar una
          emisión innecesaria puede bloquear nuevas solicitudes durante una semana.
        </Alert>
        <p>
          Se pedirá a Certbot que reemplace el certificado de{" "}
          <span className="break-all font-mono text-xs">{target?.hostname}</span>.
          El correo sigue funcionando con el certificado actual hasta que el nuevo se
          valide e instale.
        </p>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onCancel} disabled={pending}>
            Cancelar
          </Button>
          <Button type="button" onClick={onConfirm} disabled={pending || disabled}>
            {pending ? "Solicitando..." : "Forzar emisión"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
