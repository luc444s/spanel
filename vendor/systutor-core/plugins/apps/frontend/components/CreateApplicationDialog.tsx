import { FormEvent, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";

import { createApplication, inspectApplication, newIdempotencyKey } from "../api";
import type { Application, OperationSummary } from "../types";

type Props = {
  open: boolean;
  platform: boolean;
  onClose: () => void;
  onAccepted: (application: Application, operation: OperationSummary) => void;
};

export function CreateApplicationDialog({ open, platform, onClose, onAccepted }: Props) {
  const [name, setName] = useState("");
  const [repository, setRepository] = useState("");
  const [branch, setBranch] = useState("main");
  const [compose, setCompose] = useState("");
  const [domain, setDomain] = useState("");
  const [service, setService] = useState("");
  const [port, setPort] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const ingressUsed = Boolean(domain || service || port);
    if (!/^[-\w.]+\/[-\w.]+$/.test(repository)) return setError("Usá owner/repo, sin URL Git arbitraria");
    if (ingressUsed && (!domain || !service || !port)) return setError("Dominio, servicio y puerto deben completarse juntos");
    const portNumber = port ? Number(port) : null;
    if (portNumber !== null && (!Number.isInteger(portNumber) || portNumber < 1 || portNumber > 65535)) return setError("Puerto inválido");
    setPending(true);
    try {
      const application = await createApplication({
        name: name.trim(), repository: repository.trim(), branch: branch.trim(),
        compose_file: compose.trim() || null,
        domain: platform ? domain.trim() || null : null,
        ingress_service: platform ? service.trim() || null : null,
        ingress_port: platform ? portNumber : null,
      });
      const operation = await inspectApplication(application.id, newIdempotencyKey());
      setName(""); setRepository(""); setBranch("main"); setCompose("");
      setDomain(""); setService(""); setPort("");
      onAccepted(application, operation);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo crear e inspeccionar la aplicación");
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} title="Crear e inspeccionar aplicación" onClose={onClose}>
      <form className="space-y-3" onSubmit={(event) => void submit(event)}>
        <p className="text-xs text-muted-foreground">Paso 1: fuente · Paso 2: crear · Paso 3: inspección async</p>
        <label className="block space-y-1 text-sm"><span>Nombre</span><Input required value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="block space-y-1 text-sm"><span>Repositorio owner/repo</span><Input required value={repository} onChange={(e) => setRepository(e.target.value)} placeholder="owner/repo" /></label>
        <label className="block space-y-1 text-sm"><span>Rama</span><Input required value={branch} onChange={(e) => setBranch(e.target.value)} /></label>
        <label className="block space-y-1 text-sm"><span>Compose (opcional, relativo al repo)</span><Input value={compose} onChange={(e) => setCompose(e.target.value)} placeholder="compose.yaml" /></label>
        {platform ? <fieldset className="space-y-2 rounded-md border border-border p-3">
          <legend className="px-1 text-xs font-semibold">Ingress de plataforma (opcional)</legend>
          <Input value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="app.example.com" />
          <Input value={service} onChange={(e) => setService(e.target.value)} placeholder="Servicio inspeccionado" />
          <Input type="number" min={1} max={65535} value={port} onChange={(e) => setPort(e.target.value)} placeholder="Puerto 1..65535" />
        </fieldset> : null}
        {error ? <Alert title="Error">{error}</Alert> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" disabled={pending} onClick={onClose}>Cancelar</Button>
          <Button type="submit" disabled={pending}>{pending ? "Creando…" : "Crear e inspeccionar"}</Button>
        </div>
      </form>
    </Dialog>
  );
}
