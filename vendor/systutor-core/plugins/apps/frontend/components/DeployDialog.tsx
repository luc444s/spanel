import { useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input, Textarea } from "@systutor/shell/ui/input";
import { toast } from "@systutor/shell/ui/toast";

import { deployApplication, newIdempotencyKey } from "../api";
import { parseDotenv } from "../logic";
import type { Application, EnvironmentEntry, OperationSummary } from "../types";

export function DeployDialog({ application, open, blocked, onClose, onAccepted }: {
  application: Application;
  open: boolean;
  blocked: boolean;
  onClose: () => void;
  onAccepted: (operation: OperationSummary) => void;
}) {
  const [dotenv, setDotenv] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [intent, setIntent] = useState<{ key: string; body: EnvironmentEntry[] } | null>(null);

  async function submit() {
    if (blocked || confirmation !== application.slug) return;
    let request = intent;
    if (!request) {
      try {
        request = { key: newIdempotencyKey(), body: parseDotenv(dotenv) };
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "El bloque .env no es válido");
        return;
      }
    }
    setIntent(request);
    setPending(true); setError(null);
    const toastId = `apps-deploy-${application.id}`;
    toast.loading("Enviando deployment…", { id: toastId });
    try {
      const operation = await deployApplication(application.id, request.body, request.key);
      // Secrets are destroyed immediately after the server accepts the job.
      setDotenv(""); setConfirmation(""); setIntent(null);
      toast.success("Deployment aceptado; seguí el progreso en Operaciones", { id: toastId });
      onAccepted(operation);
    } catch (reason) {
      // Keep the same in-memory key/body only for an explicit retry of this uncertain request.
      const message = reason instanceof Error ? reason.message : "No se pudo aceptar el despliegue";
      setError(message);
      toast.error(message, { id: toastId });
    } finally {
      setPending(false);
    }
  }

  function close() {
    setDotenv(""); setConfirmation(""); setIntent(null); setError(null);
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
        <label className="block space-y-2 text-sm">
          <span className="font-medium">Variables en formato .env</span>
          <Textarea
            className="min-h-48 font-mono text-xs"
            value={dotenv}
            onChange={(event) => { setDotenv(event.target.value); setIntent(null); setError(null); }}
            placeholder={"DATABASE_URL=protocol://${APP_DB_USER}:${APP_DB_PASSWORD}@database:1234/${APP_DB_NAME}\nVITE_API_BASE_URL=https://api.example"}
            autoComplete="off"
            spellCheck={false}
          />
        </label>
        <p className="text-xs text-muted-foreground">
          Podés pegar un archivo .env completo. Los valores sólo viven en este diálogo y se eliminan al aceptar el deployment.
        </p>
        <p className="text-xs text-muted-foreground">
          Apps genera y conserva cifrados APP_DB_NAME, APP_DB_USER y APP_DB_PASSWORD. No los incluyas aquí; el Compose puede referenciarlos desde cualquier motor.
        </p>
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
