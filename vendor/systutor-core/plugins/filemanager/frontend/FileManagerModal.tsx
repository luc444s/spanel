import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

type Props = {
  title: string;
  subtitle: string;
  nav: ReactNode;
  actions: ReactNode;
  sidebar: ReactNode;
  footer: ReactNode;
  overlay?: ReactNode;
  escapeEnabled: boolean;
  onClose: () => void;
  children: ReactNode;
};

export function FileManagerModal({
  title,
  subtitle,
  nav,
  actions,
  sidebar,
  footer,
  overlay,
  escapeEnabled,
  onClose,
  children,
}: Props) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!escapeEnabled) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [escapeEnabled, onClose]);

  return createPortal(
    <div
      className="fixed inset-0 z-[900] flex items-stretch justify-center bg-background/80 backdrop-blur-sm sm:items-center sm:p-2"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && escapeEnabled) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Gestor de archivos"
        className="relative flex h-[100dvh] w-full flex-col overflow-hidden border-border bg-card shadow-2xl sm:h-[90vh] sm:w-[96vw] sm:max-w-[1600px] sm:rounded-xl sm:border"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-border bg-chrome px-4 py-2 text-chrome-foreground">
          <span className="text-lg" aria-hidden>📁</span>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold">{title}</h2>
            <p className="truncate text-xs text-chrome-muted">{subtitle}</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Cerrar"
            className="rounded px-2 py-1 text-lg leading-none text-chrome-muted hover:bg-chrome-foreground/10 hover:text-chrome-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            ✕
          </button>
        </div>

        <div className="border-b border-border bg-chrome px-3 py-2 text-chrome-foreground">{nav}</div>
        <div className="flex gap-2 overflow-x-auto border-b border-border bg-chrome px-3 py-2 text-chrome-foreground">{actions}</div>

        <div className="flex min-h-0 flex-1">
          <aside className="hidden w-52 shrink-0 overflow-y-auto border-r border-border bg-muted/30 p-2 md:block">
            {sidebar}
          </aside>
          <main className="relative min-w-0 flex-1 overflow-auto">{children}</main>
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-border bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground">
          {footer}
        </footer>

        {overlay}
      </div>
    </div>,
    document.body,
  );
}
