import { useEffect, useState } from "react";

import type { DataTableColumn } from "@systutor/shell/ui/data-table";
import { PaginatedDataTable } from "@systutor/shell/ui/paginated-data-table";

import type { Operation } from "../types";

export const OPERATIONS_PAGE_SIZE = 8;

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

export function operationDuration(operation: Operation) {
  if (!operation.started_at || !operation.finished_at) return "—";
  const start = new Date(operation.started_at).getTime();
  const end = new Date(operation.finished_at).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return "—";
  return `${Math.max(0, (end - start) / 1_000).toFixed(1)} s`;
}

export function ServiceOperations({ operations, emptyMessage = "No hay operaciones." }: {
  operations: Operation[];
  emptyMessage?: string;
}) {
  const [selected, setSelected] = useState<string | null>(operations[0]?.id ?? null);

  useEffect(() => {
    if (!operations.some((item) => item.id === selected)) {
      setSelected(operations[0]?.id ?? null);
    }
  }, [operations, selected]);

  const operation = operations.find((item) => item.id === selected);
  const columns: DataTableColumn<Operation>[] = [
    {
      key: "kind",
      header: "Operación",
      className: "w-32",
      render: (item) => <span className={item.id === selected ? "font-medium" : ""}>
        {item.id === selected ? "● " : ""}{item.kind}
      </span>,
    },
    {
      key: "status",
      header: "Estado",
      className: "w-28",
      render: (item) => item.status,
    },
    {
      key: "phase",
      header: "Fase",
      className: "w-28",
      render: (item) => item.phase,
    },
    {
      key: "started",
      header: "Inicio",
      className: "w-24",
      render: (item) => item.started_at ? new Date(item.started_at).toLocaleTimeString() : "—",
    },
    {
      key: "duration",
      header: "Duración",
      className: "w-24",
      render: (item) => operationDuration(item),
    },
  ];

  return <div className="space-y-2">
    <PaginatedDataTable
      columns={columns}
      rows={operations}
      rowKey={(item) => item.id}
      emptyMessage={emptyMessage}
      pageSize={OPERATIONS_PAGE_SIZE}
      dense
      label="operaciones"
      onRowClick={(item) => setSelected(item.id)}
    />
    {operation ? <div className="rounded border border-border p-2 text-sm">
      <p className="font-medium">Detalle: {operation.kind}</p>
      <dl className="mt-1 space-y-0.5 text-xs">
        <div className="flex gap-2"><dt className="text-muted-foreground">Servicio:</dt><dd>{serviceFromSubject(operation)}</dd></div>
        <div className="flex gap-2"><dt className="text-muted-foreground">Resultado:</dt><dd className="whitespace-pre-wrap">{resultText(operation)}</dd></div>
        <div className="flex gap-2"><dt className="text-muted-foreground">Operation:</dt><dd><code>{operation.id}</code></dd></div>
      </dl>
    </div> : null}
  </div>;
}
