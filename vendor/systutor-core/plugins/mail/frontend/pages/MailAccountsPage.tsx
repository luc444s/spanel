import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useEffect, useState } from "react";

import { changeMailAccountPassword, createMailAccount, listMailAccounts, mailKeys } from "../api";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { Alert } from "@systutor/shell/ui/alert";
import { Pagination } from "@systutor/shell/ui/pagination";
import { toast } from "@systutor/shell/ui/toast";

const ACCOUNT_ROWS_PER_COLUMN = 15;
const ACCOUNT_COLUMNS = 3;
const ACCOUNT_PAGE_SIZE = ACCOUNT_ROWS_PER_COLUMN * ACCOUNT_COLUMNS;

// Stable toast ids, one per operation, so the pending toast is replaced in place by the
// success or error one instead of stacking a second toast below it: a slow mail server
// keeps a single readable row instead of a moving target.
//
// The id goes in the data object, NOT as the first argument. sonner has no
// toast.success(id, message) overload: the signature is (message, data) and the id is
// read from data.id. Passing (id, message) makes the id the toast's text and spreads the
// message string into numeric keys, which produces a second, unidentifiable toast that
// spins forever.
const CREATE_TOAST = "mail:create-account";
const PASSWORD_TOAST = "mail:change-password";

// Matches the backend cache TTL (MAIL_ACCOUNTS_CACHE_TTL) so the UI does not ask for
// a refetch while the server would still answer from its own cache anyway.
const ACCOUNTS_STALE_TIME_MS = 60_000;

function useIsPortrait() {
  const [isPortrait, setIsPortrait] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const query = window.matchMedia("(orientation: portrait)");
    setIsPortrait(query.matches);

    function handleChange(event: MediaQueryListEvent) {
      setIsPortrait(event.matches);
    }

    query.addEventListener("change", handleChange);
    return () => query.removeEventListener("change", handleChange);
  }, []);

  return isPortrait;
}

export default function MailAccountsPage() {
  const queryClient = useQueryClient();
  const isPortrait = useIsPortrait();
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

  // staleTime is declared here, not in providers.tsx, so the account list is not
  // re-fetched on every navigation. Invalidation after create/password-change
  // still refetches because invalidateQueries bypasses staleTime.
  const accountsQuery = useQuery({
    queryKey: mailKeys.accounts,
    queryFn: listMailAccounts,
    staleTime: ACCOUNTS_STALE_TIME_MS,
  });
  const accounts = accountsQuery.data?.accounts ?? [];
  const totalAccountPages = Math.max(1, Math.ceil(accounts.length / ACCOUNT_PAGE_SIZE));

  useEffect(() => {
    if (accountsPage > totalAccountPages) {
      setAccountsPage(totalAccountPages);
    }
  }, [accountsPage, totalAccountPages]);

  // Both dialogs close the moment the request is dispatched, not when it resolves. The
  // mail server is reached over SSH, so waiting kept the modal open for seconds with a
  // button that said nothing. The result travels by toast instead.
  //
  // Client-side validation (mismatched or too-short passwords) is the one exception: no
  // request went out, and the error belongs next to the fields the user just typed.
  function closePasswordDialog() {
    setSelected(null);
    setNewPass("");
    setConfirmPass("");
    setError(null);
  }

  function closeCreateDialog() {
    setShowCreate(false);
    setCreateUser("");
    setCreatePass("");
    setCreateConfirm("");
    setCreateError(null);
  }

  const changeMutation = useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) => changeMailAccountPassword(email, { password }),
    onSuccess: () => toast.success("Contraseña modificada", { id: PASSWORD_TOAST }),
    // A password change does not alter the account list, so there is nothing to
    // invalidate: the only refetch that mattered was a wasted round trip to the
    // mail server sitting in front of the success toast.
    onError: (e: Error) => toast.error(e.message, { id: PASSWORD_TOAST }),
  });

  const createMutation = useMutation({
    mutationFn: createMailAccount,
    onSuccess: () => {
      // Not awaited: the new mailbox has to show up, but the user should not wait for
      // the refetch to read the confirmation.
      void queryClient.invalidateQueries({ queryKey: mailKeys.accounts });
      toast.success("Cuenta creada", { id: CREATE_TOAST });
    },
    onError: (e: Error) => toast.error(e.message, { id: CREATE_TOAST }),
  });

  function handleSubmitPassword(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    if (newPass !== confirmPass) { setError("Las contraseñas no coinciden"); return; }
    if (newPass.length < 8) { setError("Mínimo 8 caracteres"); return; }
    const email = selected;
    if (!email) return;
    closePasswordDialog();
    toast.loading("Cambiando contraseña...", { id: PASSWORD_TOAST });
    changeMutation.mutate({ email, password: newPass });
  }

  function handleSubmitCreate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setCreateError(null);
    const username = createUser.trim();
    if (!username) { setCreateError("Usuario requerido"); return; }
    if (createPass !== createConfirm) { setCreateError("Las contraseñas no coinciden"); return; }
    if (createPass.length < 8) { setCreateError("Mínimo 8 caracteres"); return; }
    const password = createPass;
    closeCreateDialog();
    toast.loading("Creando cuenta...", { id: CREATE_TOAST });
    createMutation.mutate({ username, password });
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

      <Dialog open={showCreate} title="Crear correo" onClose={closeCreateDialog}>
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
            <Button type="button" variant="secondary" size="sm" onClick={closeCreateDialog}>Cancelar</Button>
            <Button type="submit" size="sm">Crear</Button>
          </div>
        </form>
      </Dialog>

      <div className="space-y-3">
        {accounts.length === 0 ? (
          <p className="rounded-md border border-border bg-card px-3 py-4 text-sm text-muted-foreground">
            No hay correos creados para este dominio.
          </p>
        ) : (
          <div className={isPortrait ? "pb-2" : "overflow-x-auto pb-2"}>
            <div
              className={isPortrait ? undefined : "min-w-[720px]"}
              style={{
                display: "grid",
                gridTemplateColumns: isPortrait ? "1fr" : "repeat(3, minmax(0, 1fr))",
                gap: "0.75rem",
              }}
            >
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
          </div>
        )}

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-muted-foreground">
            {accounts.length} correos
          </p>
          <Pagination page={accountsPage} totalPages={totalAccountPages} onChange={setAccountsPage} />
        </div>
      </div>

      <Dialog open={Boolean(selected)} title="Cambiar contraseña" description={selected} onClose={closePasswordDialog}>
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
            <Button type="button" variant="secondary" size="sm" onClick={closePasswordDialog}>Cancelar</Button>
            <Button type="submit" size="sm">Guardar</Button>
          </div>
        </form>
      </Dialog>
    </section>
  );
}
