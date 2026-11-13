import { useRef, type MouseEvent, type TouchEvent } from "react";

import { useT } from "@/i18n/use-i18n";
import { formatDate, formatSize, formatType, type SortKey, type SortState } from "./format";
import { FileTypeIcon } from "./FileIcons";
import type { Entry } from "./types";

type Props = {
  entries: Entry[];
  selected: Set<string>;
  sort: SortState;
  onSort: (key: SortKey) => void;
  onSelect: (entry: Entry, index: number, event: MouseEvent) => void;
  onToggle: (entry: Entry, checked: boolean) => void;
  onToggleAll: (checked: boolean) => void;
  onOpen: (entry: Entry) => void;
  onContext: (entry: Entry, point: { x: number; y: number }) => void;
};

const COLUMNS: Array<{ key: SortKey | null; label: string; className: string }> = [
  { key: "name", label: "Name", className: "" },
  { key: "type", label: "Type", className: "hidden sm:table-cell" },
  { key: "size", label: "Size", className: "hidden sm:table-cell" },
  { key: "modified", label: "Modified", className: "hidden lg:table-cell" },
];

export function FileTable({
  entries,
  selected,
  sort,
  onSort,
  onSelect,
  onToggle,
  onToggleAll,
  onOpen,
  onContext,
}: Props) {
  const t = useT();
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

  const checkboxClass =
    "h-4 w-4 shrink-0 cursor-pointer appearance-none rounded-md border border-foreground/40 bg-transparent bg-center bg-no-repeat align-middle " +
    "checked:border-primary checked:bg-primary checked:bg-[url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none'%3E%3Cpath d='M3.5 8.5l2.5 2.5 6-6' stroke='white' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E\")] checked:bg-[length:12px_12px] " +
    "indeterminate:border-primary indeterminate:bg-primary indeterminate:bg-[url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none'%3E%3Cpath d='M4 8h8' stroke='white' stroke-width='2' stroke-linecap='round'/%3E%3C/svg%3E\")] indeterminate:bg-[length:12px_12px] " +
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-0";
  const allSelected = entries.length > 0 && entries.every((entry) => selected.has(entry.name));
  const someSelected = entries.some((entry) => selected.has(entry.name)) && !allSelected;

  return (
    <table className="w-full table-fixed text-left text-sm">
      <thead className="sticky top-0 z-10 bg-muted text-xs uppercase tracking-wide text-muted-foreground">
        <tr>
          <th className="w-10 p-2 align-middle">
            <div className="flex h-full items-center justify-center">
              <input
                type="checkbox"
                aria-label={t("Select all")}
                className={checkboxClass}
                checked={allSelected}
                ref={(el) => {
                  if (el) el.indeterminate = someSelected;
                }}
                onChange={(event) => onToggleAll(event.target.checked)}
              />
            </div>
          </th>
          {COLUMNS.map((column) => (
            <th key={column.label || "actions"} className={`p-2 font-medium ${column.className}`}>
              {column.key ? (
                <button
                  type="button"
                  className="flex items-center gap-1 hover:text-foreground"
                  onClick={() => onSort(column.key as SortKey)}
                >
                  {t(column.label)}
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
              {t("This folder is empty.")}
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
              <td className="p-2 align-middle">
                <div className="flex items-center">
                  <input
                    type="checkbox"
                    aria-label={t("Select {name}", { name: entry.name })}
                    checked={isSelected}
                    className={checkboxClass}
                    onClick={(event) => event.stopPropagation()}
                    onChange={(event) => onToggle(entry, event.target.checked)}
                  />
                </div>
              </td>
              <td className="truncate p-2">
                <span className="mr-2 inline-flex align-middle text-muted-foreground" aria-hidden>
                  <FileTypeIcon name={entry.name} directory={entry.directory} size={16} className="h-4 w-4" />
                </span>
                <span className={entry.directory ? "font-medium align-middle" : "align-middle"}>{entry.name}</span>
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
