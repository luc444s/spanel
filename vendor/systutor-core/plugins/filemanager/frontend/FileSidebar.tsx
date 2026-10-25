import { cn } from "@systutor/shell/ui/cn";

type Props = {
  folders: string[];
  current: string;
  onGo: (path: string) => void;
};

export function FileSidebar({ folders, current, onGo }: Props) {
  return (
    <nav aria-label="Accesos rápidos" className="space-y-1">
      <p className="px-2 pb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Accesos
      </p>
      <button
        type="button"
        onClick={() => onGo("")}
        className={cn(
          "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-accent",
          current === "" && "bg-accent font-medium",
        )}
      >
        <span aria-hidden>🏠</span> Inicio
      </button>
      {folders.map((name) => {
        const active = current === name;
        return (
          <button
            key={name}
            type="button"
            onClick={() => onGo(name)}
            className={cn(
              "flex w-full items-center gap-2 truncate rounded px-2 py-1.5 text-left text-sm hover:bg-accent",
              active && "bg-accent font-medium",
            )}
          >
            <span aria-hidden>📁</span>
            <span className="truncate">{name}</span>
          </button>
        );
      })}
      {folders.length === 0 ? (
        <p className="px-2 py-1 text-xs text-muted-foreground">Sin carpetas en la raíz.</p>
      ) : null}
    </nav>
  );
}
