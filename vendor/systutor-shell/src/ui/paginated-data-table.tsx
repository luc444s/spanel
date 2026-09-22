import { useEffect, useState } from "react";

import { DataTable, type DataTableColumn } from "./data-table";
import { Pagination } from "./pagination";

type PaginatedDataTableProps<Row> = {
  columns: DataTableColumn<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
  emptyMessage: string;
  pageSize?: number;
  dense?: boolean;
  onRowClick?: (row: Row) => void;
  label?: string;
};

export function PaginatedDataTable<Row>({
  columns,
  rows,
  rowKey,
  emptyMessage,
  pageSize = 10,
  dense = false,
  onRowClick,
  label = "registros",
}: PaginatedDataTableProps<Row>) {
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));

  useEffect(() => {
    if (page > totalPages) {
      setPage(totalPages);
    }
  }, [page, totalPages]);

  const start = (page - 1) * pageSize;
  const pagedRows = rows.slice(start, start + pageSize);

  return (
    <div className="space-y-3">
      <DataTable
        columns={columns}
        rows={pagedRows}
        rowKey={rowKey}
        emptyMessage={emptyMessage}
        dense={dense}
        onRowClick={onRowClick}
      />
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted-foreground">{rows.length} {label}</p>
        <Pagination page={page} totalPages={totalPages} onChange={setPage} />
      </div>
    </div>
  );
}
