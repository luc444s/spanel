import { InputHTMLAttributes, forwardRef } from "react";
import { cn } from "./cn";

export { Checkbox } from "./checkbox";
export { Switch } from "./switch";
export { Textarea } from "./textarea";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return (
      <input
        ref={ref}
        className={cn(
          "w-full rounded-md border border-input bg-surface px-3 py-2 text-sm text-foreground outline-none transition placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring",
          className
        )}
        {...props}
      />
    );
  }
);
