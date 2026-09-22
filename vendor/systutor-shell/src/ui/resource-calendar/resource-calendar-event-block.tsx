import type { ReactNode } from "react";
import { cn } from "../cn";

type Props = {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
};

export function ResourceCalendarEventBlock({ children, onClick, className }: Props) {
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        onClick?.();
      }}
      className={cn(
        "w-full overflow-hidden rounded-lg border border-border/70 bg-primary/10 px-2 py-1 text-left text-xs text-foreground transition hover:border-primary/40 hover:bg-primary/15",
        className,
      )}
    >
      {children}
    </button>
  );
}
