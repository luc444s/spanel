import { useRef, type MouseEvent, type TouchEvent } from "react";

import { formatDate, formatSize, formatType, type SortKey, type SortState } from "./format";
import type { Entry } from "./types";

type Props = {
  entries: Entry[];
  selected: Set<string>;
  sort: SortState;
  onSort: (key: SortKey) => void;
  onSelect: (entry: Entry, index: number, event: MouseEvent) => void;
  onToggle: (entry: Entry, checked: boolean) => void;
  onOpen: (entry: Entry) => void;
  onContext: (entry: Entry, point: { x: number; y: number }) => void;
};

const COLUMNS: Array<{ key: SortKey | null; label: string; className: string }> = [
  { key: "name", label: "Nombre", className: "" },
  { key: "type", label: "Tipo", className: "hidden sm:table-cell" },
  { key: "size", label: "Tamaño", className: "hidden sm:table-cell" },
  { key: "modified", label: "Modificado", className: "hidden lg:table-cell" },
];

export function FileTable({
  entries,
  selected,
  sort,
  onSort,
  onSelect,
  onToggle,
  onOpen,
  onContext,
}: Props) {
  const timer = useRef<number | null>(null);
  const longPressed = useRef(false);

  function startLongPress(entry: Entry, event: TouchEvent) {
    const touch = event.touches[0];
    longPressed.current = false;
    timer.current = window.setTimeout(() => {
      longPressed.current = true;
      onContext(entry, { x: touch.clientX, y: touch.clientY });
    }, 550);
  }

  function cancelLongPress() {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  }

  return (
    <table className="w-full table-fixed text-left text-sm">
      <thead className="sticky top-0 z-10 bg-muted text-xs uppercase tracking-wide text-muted-foreground">
        <tr>
          <th className="w-10 p-2" aria-label="Seleccionar" />
          {COLUMNS.map((column) => (
            <th key={column.label || "actions"} className={`p-2 font-medium ${column.className}`}>
              {column.key ? (
                <button
                  type="button"
                  className="flex items-center gap-1 hover:text-foreground"
                  onClick={() => onSort(column.key as SortKey)}
                >
                  {column.label}
                  {sort.key === column.key ? (sort.direction === "asc" ? "▲" : "▼") : ""}
                </button>
              ) : null}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {entries.length === 0 ? (
          <tr>
            <td colSpan={5} className="p-8 text-center text-muted-foreground">
              Esta carpeta está vacía.
            </td>
          </tr>
        ) : entries.map((entry, index) => {
          const isSelected = selected.has(entry.name);
          return (
            <tr
              key={entry.name}
              data-file-item="true"
              data-file-name={entry.name}
              className={`border-t border-border/70 ${isSelected ? "bg-accent/60" : "hover:bg-accent/30"}`}
              onClick={(event) => {
                if (longPressed.current) {
                  longPressed.current = false;
                  return;
                }
                onSelect(entry, index, event);
              }}
              onDoubleClick={() => onOpen(entry)}
              onContextMenu={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onContext(entry, { x: event.clientX, y: event.clientY });
              }}
              onTouchStart={(event) => startLongPress(entry, event)}
              onTouchEnd={cancelLongPress}
              onTouchMove={cancelLongPress}
              onTouchCancel={cancelLongPress}
            >
              <td className="p-2">
                <input
                  type="checkbox"
                  aria-label={`Seleccionar ${entry.name}`}
                  checked={isSelected}
                  onClick={(event) => event.stopPropagation()}
                  onChange={(event) => onToggle(entry, event.target.checked)}
                />
              </td>
              <td className="truncate p-2">
                <span className="mr-2" aria-hidden>{entry.directory ? "📁" : "📄"}</span>
                <span className={entry.directory ? "font-medium" : ""}>{entry.name}</span>
              </td>
              <td className="hidden p-2 text-muted-foreground sm:table-cell">
                {formatType(entry.name, entry.directory)}
              </td>
              <td className="hidden whitespace-nowrap p-2 text-muted-foreground sm:table-cell">
                {formatSize(entry.size)}
              </td>
              <td className="hidden whitespace-nowrap p-2 text-muted-foreground lg:table-cell">
                {formatDate(entry.modified)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
