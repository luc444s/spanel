import { type ReactNode } from "react";
import { cn } from "./cn";

export type Tab = {
  value: string;
  label: string;
  content: ReactNode;
  disabled?: boolean;
};

type TabsProps = {
  value: string;
  onChange: (value: string) => void;
  tabs: Tab[];
  className?: string;
};

export function Tabs({ value, onChange, tabs, className }: TabsProps) {
  const active = tabs.find((t) => t.value === value);

  return (
    <div className={cn("space-y-4", className)}>
      <div className="flex gap-0 border-b border-border" role="tablist">
        {tabs.map((tab) => {
          const isActive = tab.value === value;
          return (
            <button
              key={tab.value}
              role="tab"
              aria-selected={isActive}
              disabled={tab.disabled}
              onClick={() => onChange(tab.value)}
              className={cn(
                "relative px-4 py-2.5 text-sm font-medium transition",
                isActive
                  ? "text-foreground after:absolute after:-bottom-px after:left-0 after:right-0 after:h-0.5 after:bg-primary"
                  : "text-muted-foreground hover:text-foreground",
                tab.disabled && "cursor-not-allowed opacity-50"
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
      {active ? (
        <div role="tabpanel" className="pt-1">
          {active.content}
        </div>
      ) : null}
    </div>
  );
}
