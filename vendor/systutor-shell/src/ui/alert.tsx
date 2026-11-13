import { HTMLAttributes } from "react";
import { cn } from "./cn";

type AlertProps = HTMLAttributes<HTMLDivElement> & {
  title: string;
};

export function Alert({ title, children, className, ...props }: AlertProps) {
  return (
    <div
      className={cn(
        "rounded-md border border-warning/40 bg-transparent px-3 py-2 text-sm text-foreground/90",
        className
      )}
      {...props}
    >
      <p className="mb-0.5 text-sm font-medium text-warning/90">{title}</p>
      <div className="text-xs leading-relaxed text-muted-foreground">{children}</div>
    </div>
  );
}
