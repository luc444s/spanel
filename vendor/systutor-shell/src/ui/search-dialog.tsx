import { useEffect, useRef, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import { Alert } from "./alert";
import { DataTable, type DataTableColumn } from "./data-table";
import { Dialog } from "./dialog";
import { Input } from "./input";

export type SearchDialogProps<T> = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  placeholder?: string;
  columns: DataTableColumn<T>[];
  fetchFn: (query: string) => Promise<T[]>;
  onSelect: (item: T) => void;
  getRowId?: (item: T) => string;
  emptyMessage?: string;
  maxWidthClassName?: string;
};

const DEBOUNCE_MS = 300;

export function SearchDialog<T>({
  open,
  onOpenChange,
  title,
  placeholder,
  columns,
  fetchFn,
  onSelect,
  getRowId,
  emptyMessage = "Sin resultados.",
  maxWidthClassName = "max-w-4xl",
}: SearchDialogProps<T>) {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const timerRef = useRef<ReturnType<typeof setTimeout>>();
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;

  useEffect(() => {
    if (open) {
      setQuery("");
      setDebouncedQuery("");
    }
  }, [open]);

  useEffect(() => {
    timerRef.current = setTimeout(() => setDebouncedQuery(query), DEBOUNCE_MS);
    return () => clearTimeout(timerRef.current);
  }, [query]);

  const searchQuery = useQuery({
    queryKey: ["search-dialog", title, debouncedQuery],
    queryFn: () => fetchFn(debouncedQuery),
    enabled: open,
  });

  function handleSelect(item: T) {
    selectRef.current(item);
    onOpenChange(false);
  }

  return (
    <Dialog
      open={open}
      title={title}
      description={placeholder ? `Busca por ${placeholder.toLowerCase()}.` : undefined}
      onClose={() => onOpenChange(false)}
      maxWidthClassName={maxWidthClassName}
    >
      <div className="space-y-4">
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={placeholder ?? "Escribe para buscar..."}
        />
        {searchQuery.error ? (
          <Alert title="Error al buscar">{searchQuery.error.message}</Alert>
        ) : null}
        <DataTable
          columns={columns}
          rows={searchQuery.data ?? []}
          rowKey={(item) => {
            if (getRowId) return getRowId(item);
            return (item as Record<string, unknown>).id as string;
          }}
          emptyMessage={
            query.trim().length === 0
              ? "Escribe algo para buscar."
              : searchQuery.isLoading
                ? "Buscando..."
                : emptyMessage
          }
          onRowClick={handleSelect}
        />
      </div>
    </Dialog>
  );
}
