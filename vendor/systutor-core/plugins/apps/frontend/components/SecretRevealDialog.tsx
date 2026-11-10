import { FormEvent, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";

export function SecretRevealDialog({ name, pending, error, onClose, onReveal }: {
  name: string | null;
  pending: boolean;
  error: string | null;
  onClose: () => void;
  onReveal: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    if (reason.trim()) onReveal(reason.trim());
  }
  return <Dialog open={Boolean(name)} title={`Revelar ${name ?? "secreto"}`} onClose={onClose}>
    <form className="space-y-3" onSubmit={submit}>
      <p className="text-sm">Esta acción quedará registrada en la auditoría.</p>
      <p className="text-sm text-muted-foreground">El valor podría permitir acceso a datos de la aplicación. No lo compartas ni lo incluyas en logs.</p>
      <label className="block space-y-1 text-sm"><span>Motivo *</span><Input value={reason} maxLength={500} autoFocus onChange={(event) => setReason(event.target.value)} /></label>
      {error ? <Alert title="No se pudo revelar">{error}</Alert> : null}
      <div className="flex justify-end gap-2"><Button type="button" size="sm" variant="secondary" onClick={onClose}>Cancelar</Button><Button type="submit" size="sm" disabled={pending || !reason.trim()}>Revelar durante 30 s</Button></div>
    </form>
  </Dialog>;
}
