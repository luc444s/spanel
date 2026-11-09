import { useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";

import { deleteApplication, newIdempotencyKey } from "../api";
import type { Application, OperationSummary } from "../types";

export function DeleteDialog({ application, open, onClose, onAccepted }: {
  application: Application; open: boolean; onClose: () => void;
  onAccepted: (operation: OperationSummary) => void;
}) {
  const [confirmation, setConfirmation] = useState("");
  const [purge, setPurge] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit() {
    setPending(true); setError(null);
    try {
      const operation = await deleteApplication(application.id, confirmation, purge, newIdempotencyKey());
      setConfirmation(""); setPurge(false); onAccepted(operation);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo aceptar el borrado");
    } finally { setPending(false); }
  }
  return <Dialog open={open} title="Eliminar aplicación" onClose={onClose} maxWidthClassName="max-w-md">
    <div className="space-y-3 text-sm">
      <Alert title="Acción destructiva">El éxito sólo se confirmará cuando el job llegue a estado terminal.</Alert>
      <p>Escribí <code>{application.slug}</code> para confirmar.</p>
      <Input value={confirmation} onChange={(e) => setConfirmation(e.target.value)} />
      <label className="flex gap-2"><input type="checkbox" checked={purge} onChange={(e) => setPurge(e.target.checked)} /> Purgar también datos persistentes</label>
      {error ? <Alert title="Error">{error}</Alert> : null}
      <div className="flex justify-end gap-2"><Button variant="secondary" onClick={onClose}>Cancelar</Button><Button disabled={pending || confirmation !== application.slug} onClick={() => void submit()}>Eliminar</Button></div>
    </div>
  </Dialog>;
}
