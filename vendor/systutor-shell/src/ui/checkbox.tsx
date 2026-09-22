import { InputHTMLAttributes, forwardRef } from "react";
import { cn } from "./cn";

export const Checkbox = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Checkbox({ className, ...props }, ref) {
    return (
      <input
        ref={ref}
        type="checkbox"
        className={cn(
          "h-4 w-4 rounded border-border bg-surface text-primary accent-primary focus:ring-2 focus:ring-ring focus:ring-offset-1 focus:ring-offset-background",
          className
        )}
        {...props}
      />
    );
  }
);
