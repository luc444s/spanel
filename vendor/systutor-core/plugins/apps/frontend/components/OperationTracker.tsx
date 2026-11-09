import { useEffect, useRef } from "react";

import { toast } from "@systutor/shell/ui/toast";

import { isTerminalOperation } from "../logic";
import type { Operation } from "../types";
import { useOperationPolling } from "../useOperationPolling";

const PHASES: Record<string, string> = {
  queued: "En cola", cloning: "Clonando", auditing: "Auditando", staging: "Preparando",
  building: "Construyendo", verifying_images: "Verificando imágenes", starting: "Iniciando",
  verifying_runtime: "Verificando runtime", publishing: "Publicando", complete: "Completada",
  failed: "Fallida",
};

export function OperationTracker({ operationId, applicationId, onUpdate }: {
  operationId: string;
  applicationId: string;
  onUpdate: (operation: Operation) => void;
}) {
  const query = useOperationPolling(operationId, applicationId);
  const notified = useRef(false);
  useEffect(() => {
    if (!query.data) return;
    onUpdate(query.data);
    if (!isTerminalOperation(query.data) || notified.current) return;
    notified.current = true;
    if (query.data.status === "succeeded") toast.success(`${query.data.kind}: operación completada`);
    else toast.error(query.data.public_error || "La operación falló");
  }, [onUpdate, query.data]);

  const operation = query.data;
  return <div className="rounded-md border border-border bg-card px-3 py-2 text-sm">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="font-medium">{operation?.kind ?? "Operación"}</span>
      <span className="text-muted-foreground">{PHASES[operation?.phase ?? "queued"] ?? operation?.phase}</span>
    </div>
    <code className="text-xs text-muted-foreground">{operationId}</code>
    {query.error ? <p className="text-destructive">No se pudo consultar la operación.</p> : null}
  </div>;
}
