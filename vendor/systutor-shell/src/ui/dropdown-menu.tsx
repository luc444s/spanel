import { type ReactNode, useEffect, useRef, useState } from "react";
import { cn } from "./cn";

export type DropdownItem = {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  destructive?: boolean;
  icon?: ReactNode;
};

type DropdownMenuProps = {
  trigger: ReactNode;
  items: DropdownItem[];
  align?: "start" | "end";
  className?: string;
};

export function DropdownMenu({
  trigger,
  items,
  align = "start",
  className,
}: DropdownMenuProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const alignClass = align === "end" ? "right-0" : "left-0";

  return (
    <div ref={containerRef} className={cn("relative inline-flex", className)}>
      <button type="button" onClick={() => setOpen((prev) => !prev)}>
        {trigger}
      </button>
      {open ? (
        <div
          className={cn(
            "absolute top-full z-50 mt-1 min-w-[10rem] overflow-hidden rounded-md border border-border bg-popover shadow-lg",
            alignClass
          )}
        >
          {items.map((item, index) => (
            <button
              key={item.label + String(index)}
              type="button"
              disabled={item.disabled}
              onClick={() => {
                item.onClick();
                setOpen(false);
              }}
              className={cn(
                "flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition",
                item.destructive
                  ? "text-destructive hover:bg-destructive/10"
                  : "text-popover-foreground hover:bg-accent hover:text-accent-foreground",
                item.disabled && "cursor-not-allowed opacity-50"
              )}
            >
              {item.icon ? <span className="h-4 w-4 shrink-0">{item.icon}</span> : null}
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
