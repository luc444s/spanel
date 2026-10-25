import { useRef, type MouseEvent, type TouchEvent } from "react";

import { formatSize, formatType } from "./format";
import { fileIcon } from "./fileUtils";
import type { Entry } from "./types";

type Props = {
  entries: Entry[];
  selected: Set<string>;
  onSelect: (entry: Entry, index: number, event: MouseEvent) => void;
  onToggle: (entry: Entry, checked: boolean) => void;
  onOpen: (entry: Entry) => void;
  onContext: (entry: Entry, point: { x: number; y: number }) => void;
};

export function FileGrid({ entries, selected, onSelect, onToggle, onOpen, onContext }: Props) {
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

  if (entries.length === 0) {
    return <p className="p-8 text-center text-sm text-muted-foreground">Esta carpeta está vacía.</p>;
  }

  return (
    <div className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
      {entries.map((entry, index) => {
        const isSelected = selected.has(entry.name);
        return (
          <div
            key={entry.name}
            role="button"
            tabIndex={0}
            data-file-item="true"
            data-file-name={entry.name}
            aria-label={entry.name}
            aria-pressed={isSelected}
            className={`relative flex cursor-default select-none flex-col items-center gap-1 rounded-lg border p-3 text-center transition focus:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
              isSelected
                ? "border-primary bg-accent/70"
                : "border-transparent hover:border-border hover:bg-accent/30"
            }`}
            onClick={(event) => {
              if (longPressed.current) {
                longPressed.current = false;
                return;
              }
              onSelect(entry, index, event);
            }}
            onDoubleClick={() => onOpen(entry)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onOpen(entry);
              }
            }}
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
            <input
              type="checkbox"
              aria-label={`Seleccionar ${entry.name}`}
              checked={isSelected}
              className="absolute left-2 top-2"
              onClick={(event) => event.stopPropagation()}
              onChange={(event) => onToggle(entry, event.target.checked)}
            />
            <span className="text-4xl leading-none" aria-hidden>{fileIcon(entry.name, entry.directory)}</span>
            <p className="line-clamp-2 w-full break-all text-xs font-medium">{entry.name}</p>
            <p className="text-[11px] text-muted-foreground">
              {entry.directory ? formatType(entry.name, true) : `${formatType(entry.name, false)} · ${formatSize(entry.size)}`}
            </p>
          </div>
        );
      })}
    </div>
  );
}
