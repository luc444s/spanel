type ShellHeaderProps = {
  tenantName: string | null;
  branchName: string | null;
  userName: string | null;
  userEmail: string | null;
};

export function ShellHeader({
  tenantName,
  branchName,
  userName,
  userEmail,
}: ShellHeaderProps) {
  return (
    <div className="space-y-1">
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>
          Tenant: <span className="text-foreground">{tenantName ?? "Sin tenant"}</span>
        </span>
        <span>
          Branch: <span className="text-foreground">{branchName ?? "Sin branch"}</span>
        </span>
        <span>
          User: <span className="text-foreground">{userName ?? userEmail ?? "Sesion activa"}</span>
        </span>
      </div>
    </div>
  );
}
