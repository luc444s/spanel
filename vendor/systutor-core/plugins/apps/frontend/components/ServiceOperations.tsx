import { useState } from "react";

import type { Operation } from "../types";

export function serviceFromSubject(operation: Operation) {
  const subject = operation.subject_id ?? "";
  return subject.includes(":") ? subject.slice(subject.indexOf(":") + 1) : "aplicación";
}

export function resultText(operation: Operation | undefined) {
  if (!operation) return "";
  const output = operation.result?.output;
  if (typeof output === "string" && output.trim()) return output.trim().slice(0, 2_000);
  return operation.public_error ?? "Sin detalle público.";
}

export function ServiceOperations({ operations }: { operations: Operation[] }) {
  const [selected, setSelected] = useState<string | null>(operations[0]?.id ?? null);
  const operation = operations.find((item) => item.id === selected);
  return <div className="space-y-3">
    <div className="overflow-x-auto"><table className="w-full text-left text-sm">
      <thead><tr><th className="p-2">Operación</th><th className="p-2">Estado</th><th className="p-2">Fase</th><th className="p-2">Inicio</th><th className="p-2">Duración</th></tr></thead>
      <tbody>{operations.map((item) => {
        const start = item.started_at ? new Date(item.started_at) : null;
        const end = item.finished_at ? new Date(item.finished_at) : null;
        return <tr className={`cursor-pointer border-t border-border ${item.id === selected ? "bg-muted" : ""}`} key={item.id} onClick={() => setSelected(item.id)}>
          <td className="p-2">{item.kind}</td><td className="p-2">{item.status}</td><td className="p-2">{item.phase}</td>
          <td className="p-2">{start?.toLocaleTimeString() ?? "—"}</td>
          <td className="p-2">{start && end ? `${((end.getTime() - start.getTime()) / 1000).toFixed(1)} s` : "—"}</td>
        </tr>;
      })}</tbody>
    </table></div>
    {operations.length === 0 ? <p className="text-sm text-muted-foreground">No hay operaciones para este servicio.</p> : null}
    {operation ? <div className="rounded border border-border p-3 text-sm">
      <p className="font-medium">Detalle: {operation.kind}</p>
      <dl className="mt-2 space-y-1">
        <div className="flex gap-2"><dt className="text-muted-foreground">Servicio:</dt><dd>{serviceFromSubject(operation)}</dd></div>
        <div className="flex gap-2"><dt className="text-muted-foreground">Resultado:</dt><dd className="whitespace-pre-wrap">{resultText(operation)}</dd></div>
        <div className="flex gap-2"><dt className="text-muted-foreground">Operation:</dt><dd><code>{operation.id}</code></dd></div>
      </dl>
    </div> : null}
  </div>;
}
