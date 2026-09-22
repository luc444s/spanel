import { ReactNode } from "react";
import { cn } from "./cn";

export type DataTableColumn<Row> = {
  key: string;
  header: string;
  className?: string;
  render: (row: Row) => ReactNode;
};

type DataTableProps<Row> = {
  columns: DataTableColumn<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
  emptyMessage: string;
  onRowClick?: (row: Row) => void;
  onRowDoubleClick?: (row: Row) => void;
  dense?: boolean;
};

export function DataTable<Row>({ columns, rows, rowKey, emptyMessage, onRowClick, onRowDoubleClick, dense = false }: DataTableProps<Row>) {
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-border text-sm text-card-foreground">
          <thead className="bg-muted/50 text-left text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              {columns.map((column) => (
                <th key={column.key} className={cn(dense ? "px-3 py-1.5 font-semibold" : "px-4 py-3 font-semibold", column.className)}>
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rows.length > 0 ? (
              rows.map((row) => (
                <tr
                  key={rowKey(row)}
                  className={cn("align-top", (onRowClick || onRowDoubleClick) && "cursor-pointer hover:bg-accent/50")}
                  onClick={() => onRowClick?.(row)}
                  onDoubleClick={() => onRowDoubleClick?.(row)}
                >
                  {columns.map((column) => (
                    <td key={column.key} className={cn(dense ? "px-3 py-1.5" : "px-4 py-3", column.className)}>
                      {column.render(row)}
                    </td>
                  ))}
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={columns.length} className="px-4 py-8 text-center text-muted-foreground">
                  {emptyMessage}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
