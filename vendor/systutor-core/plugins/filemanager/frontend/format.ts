import type { Entry } from "./types";

const SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

export function formatSize(size: number | null): string {
  if (size === null) return "—";
  if (size < 1024) return `${size} B`;
  let value = size;
  let unit = 0;
  while (value >= 1024 && unit < SIZE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${SIZE_UNITS[unit]}`;
}

const dateFormatter = new Intl.DateTimeFormat("es-AR", {
  dateStyle: "short",
  timeStyle: "short",
});

export function formatDate(modified: number): string {
  if (!modified) return "—";
  return dateFormatter.format(new Date(modified * 1000));
}

export function formatType(name: string, directory: boolean): string {
  if (directory) return "Carpeta";
  const dot = name.lastIndexOf(".");
  if (dot <= 0 || dot === name.length - 1) return "Archivo";
  return name.slice(dot + 1).toUpperCase();
}

export type SortKey = "name" | "type" | "size" | "modified";
export type SortState = { key: SortKey; direction: "asc" | "desc" };

function compare(left: Entry, right: Entry, key: SortKey): number {
  if (key === "name") return left.name.localeCompare(right.name, "es", { numeric: true });
  if (key === "type") return formatType(left.name, left.directory).localeCompare(formatType(right.name, right.directory));
  if (key === "size") return (left.size ?? -1) - (right.size ?? -1);
  return left.modified - right.modified;
}

export function sortEntries(entries: Entry[], sort: SortState): Entry[] {
  const factor = sort.direction === "asc" ? 1 : -1;
  return [...entries].sort((left, right) => {
    if (left.directory !== right.directory) return left.directory ? -1 : 1;
    return compare(left, right, sort.key) * factor;
  });
}
