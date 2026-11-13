import { ButtonHTMLAttributes, forwardRef } from "react";
import { cn } from "./cn";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost";
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = "primary", ...props },
  ref
) {
  return (
    <button
      ref={ref}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60",
        variant === "primary"
          ? "bg-primary text-primary-foreground hover:bg-primary/90"
          : variant === "ghost"
            ? "border border-transparent bg-transparent text-foreground hover:bg-accent/40 hover:border-border/60"
            : "border border-border bg-secondary text-secondary-foreground hover:bg-accent/40 hover:border-border/60",
        className
      )}
      {...props}
    />
  );
});
