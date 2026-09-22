import { HTMLAttributes } from "react";
import { cn } from "./cn";

type AlertProps = HTMLAttributes<HTMLDivElement> & {
  title: string;
};

export function Alert({ title, children, className, ...props }: AlertProps) {
  return (
    <div
      className={cn(
        "rounded-md border border-amber-300 dark:border-amber-700/50 bg-amber-50 dark:bg-amber-500/10 p-4 text-sm text-amber-800 dark:text-amber-100",
        className
      )}
      {...props}
    >
      <p className="mb-1 font-medium">{title}</p>
      <div className="text-amber-700 dark:text-amber-50/90">{children}</div>
    </div>
  );
}
