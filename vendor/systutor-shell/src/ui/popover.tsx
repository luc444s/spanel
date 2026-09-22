import { type ReactNode, useEffect, useRef, useState } from "react";
import { cn } from "./cn";

type PopoverProps = {
  trigger: ReactNode;
  children: ReactNode;
  align?: "start" | "center" | "end";
  className?: string;
  contentClassName?: string;
};

export function Popover({
  trigger,
  children,
  align = "center",
  className,
  contentClassName,
}: PopoverProps) {
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

  const alignClass =
    align === "start" ? "left-0" : align === "end" ? "right-0" : "left-1/2 -translate-x-1/2";

  return (
    <div ref={containerRef} className={cn("relative inline-flex", className)}>
      <button type="button" onClick={() => setOpen((prev) => !prev)}>
        {trigger}
      </button>
      {open ? (
        <div
          className={cn(
            "absolute top-full z-50 mt-1 min-w-[10rem] rounded-md border border-border bg-popover shadow-lg",
            alignClass,
            contentClassName
          )}
        >
          {children}
        </div>
      ) : null}
    </div>
  );
}
