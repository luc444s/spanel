import { useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";

import { deployApplication, newIdempotencyKey } from "../api";
import type { Application, EnvironmentEntry, OperationSummary } from "../types";

export function DeployDialog({ application, open, blocked, onClose, onAccepted }: {
  application: Application;
  open: boolean;
  blocked: boolean;
  onClose: () => void;
  onAccepted: (operation: OperationSummary) => void;
}) {
  const [environment, setEnvironment] = useState<EnvironmentEntry[]>([]);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [intent, setIntent] = useState<{ key: string; body: EnvironmentEntry[] } | null>(null);

  function addVariable() {
    if (!name.trim()) return;
    setEnvironment((current) => [...current.filter((item) => item.name !== name.trim()), { name: name.trim(), value }]);
    setName(""); setValue("");
  }

  async function submit() {
    if (blocked || confirmation !== application.slug) return;
    const request = intent ?? { key: newIdempotencyKey(), body: environment.map((entry) => ({ ...entry })) };
    setIntent(request);
    setPending(true); setError(null);
    try {
      const operation = await deployApplication(application.id, request.body, request.key);
      // Secrets are destroyed immediately after the server accepts the job.
      setEnvironment([]); setName(""); setValue(""); setConfirmation(""); setIntent(null);
      onAccepted(operation);
    } catch (reason) {
      // Keep the same in-memory key/body only for an explicit retry of this uncertain request.
      setError(reason instanceof Error ? reason.message : "No se pudo aceptar el despliegue");
    } finally {
      setPending(false);
    }
  }

  function close() {
    setEnvironment([]); setName(""); setValue(""); setConfirmation(""); setIntent(null); setError(null);
    onClose();
  }

  return <Dialog open={open} title={`Desplegar ${application.name}`} onClose={close}>
    <div className="space-y-4">
      <Alert title="Código no confiable">Docker build ejecutará código del repositorio. Revisá la auditoría antes de continuar.</Alert>
      <dl className="grid grid-cols-2 gap-2 text-sm">
        <div><dt className="text-muted-foreground">Dominio</dt><dd>{application.domain ?? "Sin publicar"}</dd></div>
        <div><dt className="text-muted-foreground">Ingress</dt><dd>{application.ingress_service ? `${application.ingress_service}:${application.ingress_port}` : "Sin ingress"}</dd></div>
      </dl>
      <div className="space-y-2 rounded-md border border-border p-3">
        <p className="text-sm font-medium">Variables (sólo viven en este diálogo)</p>
        {environment.map((entry) => <div key={entry.name} className="flex items-center justify-between text-sm"><code>{entry.name}</code><Button variant="secondary" size="sm" onClick={() => setEnvironment((items) => items.filter((item) => item.name !== entry.name))}>Quitar</Button></div>)}
        <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="NOMBRE" autoComplete="off" />
          <Input type="password" value={value} onChange={(e) => setValue(e.target.value)} placeholder="Valor" autoComplete="new-password" />
          <Button type="button" variant="secondary" onClick={addVariable}>Añadir</Button>
        </div>
      </div>
      <label className="block space-y-1 text-sm"><span>Escribí <code>{application.slug}</code> para confirmar</span><Input value={confirmation} onChange={(e) => setConfirmation(e.target.value)} /></label>
      {blocked ? <Alert title="Despliegue bloqueado">La última auditoría contiene errores. No existe bypass.</Alert> : null}
      {error ? <Alert title="Error">{error}</Alert> : null}
      <div className="flex justify-end gap-2">
        <Button variant="secondary" disabled={pending} onClick={close}>Cancelar</Button>
        <Button disabled={pending || blocked || confirmation !== application.slug} onClick={() => void submit()}>{pending ? "Aceptando…" : intent ? "Reintentar misma solicitud" : "Desplegar"}</Button>
      </div>
    </div>
  </Dialog>;
}
