import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";

import { changeMailAccountPassword, createMailAccount, listMailAccounts, mailKeys } from "../api";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { Alert } from "@systutor/shell/ui/alert";
import { Pagination } from "@systutor/shell/ui/pagination";

const ACCOUNT_ROWS_PER_COLUMN = 15;
const ACCOUNT_COLUMNS = 3;
const ACCOUNT_PAGE_SIZE = ACCOUNT_ROWS_PER_COLUMN * ACCOUNT_COLUMNS;

export default function MailAccountsPage() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [newPass, setNewPass] = useState("");
  const [confirmPass, setConfirmPass] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createUser, setCreateUser] = useState("");
  const [createPass, setCreatePass] = useState("");
  const [createConfirm, setCreateConfirm] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [accountsPage, setAccountsPage] = useState(1);

  const accountsQuery = useQuery({ queryKey: mailKeys.accounts, queryFn: listMailAccounts });
  const accounts = accountsQuery.data?.accounts ?? [];
  const totalAccountPages = Math.max(1, Math.ceil(accounts.length / ACCOUNT_PAGE_SIZE));

  useEffect(() => {
    if (accountsPage > totalAccountPages) {
      setAccountsPage(totalAccountPages);
    }
  }, [accountsPage, totalAccountPages]);

  const changeMutation = useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) => changeMailAccountPassword(email, { password }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: mailKeys.accounts });
      setSelected(null);
      setNewPass("");
      setConfirmPass("");
      setError(null);
      toast.success("Contraseña actualizada");
    },
    onError: (e: Error) => setError(e.message),
  });

  const createMutation = useMutation({
    mutationFn: createMailAccount,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: mailKeys.accounts });
      setShowCreate(false);
      setCreateUser("");
      setCreatePass("");
      setCreateConfirm("");
      setCreateError(null);
      toast.success("Cuenta creada");
    },
    onError: (e: Error) => setCreateError(e.message),
  });

  function handleSubmitPassword(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    if (newPass !== confirmPass) { setError("Las contraseñas no coinciden"); return; }
    if (newPass.length < 8) { setError("Mínimo 8 caracteres"); return; }
    changeMutation.mutate({ email: selected!, password: newPass });
  }

  function handleSubmitCreate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setCreateError(null);
    if (!createUser.trim()) { setCreateError("Usuario requerido"); return; }
    if (createPass !== createConfirm) { setCreateError("Las contraseñas no coinciden"); return; }
    if (createPass.length < 8) { setCreateError("Mínimo 8 caracteres"); return; }
    createMutation.mutate({ username: createUser, password: createPass });
  }

  if (accountsQuery.isLoading) {
    return <div className="flex min-h-[200px] items-center justify-center text-sm text-muted-foreground">Cargando...</div>;
  }

  if (accountsQuery.error) {
    return <Alert title="Error">No se pudieron cargar las cuentas.</Alert>;
  }

  const domain = accountsQuery.data?.domain ?? "";
  const pageStart = (accountsPage - 1) * ACCOUNT_PAGE_SIZE;
  const visibleAccounts = accounts.slice(pageStart, pageStart + ACCOUNT_PAGE_SIZE);
  const accountColumns = Array.from({ length: ACCOUNT_COLUMNS }, (_, columnIndex) =>
    visibleAccounts.slice(
      columnIndex * ACCOUNT_ROWS_PER_COLUMN,
      (columnIndex + 1) * ACCOUNT_ROWS_PER_COLUMN,
    ),
  );

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-foreground">Correos — {domain}</h1>
        <Button size="sm" onClick={() => { setShowCreate(true); setCreateError(null); }}>
          Agregar correo
        </Button>
      </div>

      <Dialog open={showCreate} title="Crear correo" onClose={() => setShowCreate(false)}>
        <form className="space-y-3" onSubmit={handleSubmitCreate}>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Usuario</span>
            <div className="flex items-center gap-0">
              <Input type="text" value={createUser} onChange={(e) => setCreateUser(e.target.value)} placeholder="ventas" autoFocus className="rounded-r-none" />
              <span className="inline-flex items-center rounded-r-md border border-l-0 border-input bg-muted px-2 text-sm text-muted-foreground">@{domain}</span>
            </div>
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Contraseña</span>
            <Input type="password" value={createPass} onChange={(e) => setCreatePass(e.target.value)} placeholder="Mínimo 8 caracteres" />
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Confirmar contraseña</span>
            <Input type="password" value={createConfirm} onChange={(e) => setCreateConfirm(e.target.value)} placeholder="Repetir contraseña" />
          </label>
          {createError && <Alert title="Error">{createError}</Alert>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={() => setShowCreate(false)}>Cancelar</Button>
            <Button type="submit" size="sm" disabled={createMutation.isPending}>{createMutation.isPending ? "..." : "Crear"}</Button>
          </div>
        </form>
      </Dialog>

      <div className="space-y-3">
        {accounts.length === 0 ? (
          <p className="rounded-md border border-border bg-card px-3 py-4 text-sm text-muted-foreground">
            No hay correos creados para este dominio.
          </p>
        ) : (
          <div className="grid gap-3 md:grid-cols-3">
            {accountColumns.map((column, columnIndex) => (
              <div key={columnIndex} className="space-y-1 rounded-md border border-border bg-card p-2">
                {column.map((email) => (
                  <button
                    key={email}
                    type="button"
                    onClick={() => { setSelected(email); setNewPass(""); setConfirmPass(""); setError(null); }}
                    className="block w-full truncate rounded px-2 py-1 text-left text-sm text-primary hover:bg-accent hover:underline"
                    title={email}
                  >
                    {email}
                  </button>
                ))}
              </div>
            ))}
          </div>
        )}

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-muted-foreground">
            {accounts.length} correos
          </p>
          <Pagination page={accountsPage} totalPages={totalAccountPages} onChange={setAccountsPage} />
        </div>
      </div>

      <Dialog open={Boolean(selected)} title="Cambiar contraseña" description={selected} onClose={() => setSelected(null)}>
        <form className="space-y-3" onSubmit={handleSubmitPassword}>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Nueva contraseña</span>
            <Input type="password" value={newPass} onChange={(e) => setNewPass(e.target.value)} placeholder="Mínimo 8 caracteres" autoFocus />
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Confirmar contraseña</span>
            <Input type="password" value={confirmPass} onChange={(e) => setConfirmPass(e.target.value)} placeholder="Repetir contraseña" />
          </label>
          {error && <Alert title="Error">{error}</Alert>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={() => setSelected(null)}>Cancelar</Button>
            <Button type="submit" size="sm" disabled={changeMutation.isPending}>{changeMutation.isPending ? "..." : "Guardar"}</Button>
          </div>
        </form>
      </Dialog>
    </section>
  );
}
