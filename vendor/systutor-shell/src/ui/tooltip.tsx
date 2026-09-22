import { type ReactNode, useEffect, useRef, useState } from "react";
import { cn } from "./cn";

type TooltipProps = {
  content: string;
  children: ReactNode;
  className?: string;
};

export function Tooltip({ content, children, className }: TooltipProps) {
  const [visible, setVisible] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const triggerRef = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);

  function show() {
    if (!triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    setPosition({
      top: rect.top - 8,
      left: rect.left + rect.width / 2,
    });
    setVisible(true);
  }

  useEffect(() => {
    if (!visible || !tipRef.current) return;
    const tipRect = tipRef.current.getBoundingClientRect();
    if (position.left - tipRect.width / 2 < 0) {
      setPosition((p) => ({ ...p, left: tipRect.width / 2 }));
    }
  }, [visible, position.left]);

  return (
    <div
      ref={triggerRef}
      className={cn("relative inline-flex", className)}
      onMouseEnter={show}
      onMouseLeave={() => setVisible(false)}
      onFocus={show}
      onBlur={() => setVisible(false)}
    >
      {children}
      {visible ? (
        <div
          ref={tipRef}
          role="tooltip"
          className="pointer-events-none absolute z-50 -translate-x-1/2 -translate-y-full"
          style={{ top: position.top, left: position.left }}
        >
          <div className="rounded-md bg-foreground px-2.5 py-1.5 text-xs text-background shadow-lg">
            {content}
          </div>
          <div className="mx-auto h-0 w-0 -translate-y-px border-l-4 border-r-4 border-t-4 border-transparent border-t-foreground" />
        </div>
      ) : null}
    </div>
  );
}
