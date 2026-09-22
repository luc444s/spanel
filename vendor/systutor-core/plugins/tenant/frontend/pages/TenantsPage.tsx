import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { toast } from "sonner";

import {
  assignUserToTenant,
  createTenant,
  createUserInTenant,
  listTenants,
  listTenantUsers,
  reassignUserFromTenant,
  tenantKeys,
  updateTenantDomain,
  type TenantResponse,
  type TenantUserResponse,
} from "../api";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { Alert } from "@systutor/shell/ui/alert";

export default function TenantsPage() {
  const queryClient = useQueryClient();

  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createSlug, setCreateSlug] = useState("");
  const [createDomain, setCreateDomain] = useState("");
  const [createUserEmail, setCreateUserEmail] = useState("");
  const [createUserPassword, setCreateUserPassword] = useState("");
  const [createUserFullName, setCreateUserFullName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);

  const [selectedTenant, setSelectedTenant] = useState<TenantResponse | null>(null);
  const [editDomain, setEditDomain] = useState("");
  const [editError, setEditError] = useState<string | null>(null);

  const [showUsers, setShowUsers] = useState<TenantResponse | null>(null);
  const [newUserEmail, setNewUserEmail] = useState("");
  const [newUserPassword, setNewUserPassword] = useState("");
  const [newUserFullName, setNewUserFullName] = useState("");
  const [newUserError, setNewUserError] = useState<string | null>(null);
  const [reassignUserId, setReassignUserId] = useState<string | null>(null);
  const [reassignTargetTenantId, setReassignTargetTenantId] = useState("");
  const [reassignError, setReassignError] = useState<string | null>(null);

  const tenantsQuery = useQuery({ queryKey: tenantKeys.tenants, queryFn: listTenants });

  const usersQuery = useQuery({
    queryKey: tenantKeys.users(showUsers?.id ?? ""),
    queryFn: () => listTenantUsers(showUsers!.id),
    enabled: Boolean(showUsers),
  });

  const createMutation = useMutation({
    mutationFn: createTenant,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: tenantKeys.tenants });
      setShowCreate(false);
      setCreateName("");
      setCreateSlug("");
      setCreateDomain("");
      setCreateUserEmail("");
      setCreateUserPassword("");
      setCreateUserFullName("");
      setCreateError(null);
      toast.success("Tenant creado");
    },
    onError: (e: Error) => setCreateError(e.message),
  });

  const updateDomainMutation = useMutation({
    mutationFn: ({ tenantId, domain }: { tenantId: string; domain: string | null }) =>
      updateTenantDomain(tenantId, { domain }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: tenantKeys.tenants });
      setSelectedTenant(null);
      setEditError(null);
      toast.success("Dominio actualizado");
    },
    onError: (e: Error) => setEditError(e.message),
  });

  const createUserMutation = useMutation({
    mutationFn: ({ tenantId, email, password, full_name }: { tenantId: string; email: string; password: string; full_name: string }) =>
      createUserInTenant(tenantId, { email, password, full_name }),
    onSuccess: async (_, vars) => {
      await queryClient.invalidateQueries({ queryKey: tenantKeys.users(vars.tenantId) });
      setNewUserEmail("");
      setNewUserPassword("");
      setNewUserFullName("");
      setNewUserError(null);
      toast.success("Usuario creado");
    },
    onError: (e: Error) => setNewUserError(e.message),
  });

  const removeMutation = useMutation({
    mutationFn: ({ tenantId, userId, targetTenantId }: { tenantId: string; userId: string; targetTenantId: string }) =>
      reassignUserFromTenant(tenantId, userId, { target_tenant_id: targetTenantId }),
    onSuccess: async (_, vars) => {
      await queryClient.invalidateQueries({ queryKey: tenantKeys.users(vars.tenantId) });
      setReassignUserId(null);
      setReassignTargetTenantId("");
      setReassignError(null);
      toast.success("Usuario reasignado");
    },
    onError: (e: Error) => setReassignError(e.message),
  });

  function handleCreate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setCreateError(null);
    if (!createName.trim()) { setCreateError("Nombre requerido"); return; }
    if (!createSlug.trim()) { setCreateError("Slug requerido"); return; }
    if (!createUserEmail.trim()) { setCreateError("Email del usuario requerido"); return; }
    if (!createUserPassword.trim()) { setCreateError("Contraseña del usuario requerida"); return; }
    if (createUserPassword.length < 8) { setCreateError("Contraseña mínimo 8 caracteres"); return; }
    createMutation.mutate({
      name: createName,
      slug: createSlug,
      domain: createDomain || undefined,
      user_email: createUserEmail,
      user_password: createUserPassword,
      user_full_name: createUserFullName || undefined,
    });
  }

  function handleEditDomain(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setEditError(null);
    if (!selectedTenant) return;
    updateDomainMutation.mutate({ tenantId: selectedTenant.id, domain: editDomain || null });
  }

  function handleCreateUser(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setNewUserError(null);
    if (!showUsers) return;
    if (!newUserEmail.trim()) { setNewUserError("Email requerido"); return; }
    if (!newUserPassword.trim()) { setNewUserError("Contraseña requerida"); return; }
    if (newUserPassword.length < 8) { setNewUserError("Contraseña mínimo 8 caracteres"); return; }
    createUserMutation.mutate({
      tenantId: showUsers.id,
      email: newUserEmail,
      password: newUserPassword,
      full_name: newUserFullName,
    });
  }

  if (tenantsQuery.isLoading) {
    return <div className="flex min-h-[200px] items-center justify-center text-sm text-muted-foreground">Cargando...</div>;
  }

  if (tenantsQuery.error) {
    return <Alert title="Error">No se pudieron cargar los tenants.</Alert>;
  }

  const tenants = tenantsQuery.data?.tenants ?? [];

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-foreground">Tenants</h1>
        <Button size="sm" onClick={() => { setShowCreate(true); setCreateError(null); }}>
        Crear tenant
      </Button>
    </div>

      <Dialog open={showCreate} title="Crear tenant" onClose={() => setShowCreate(false)}>
        <form className="space-y-3" onSubmit={handleCreate}>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Nombre</span>
            <Input type="text" value={createName} onChange={(e) => setCreateName(e.target.value)} placeholder="Mi Empresa" autoFocus />
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Slug</span>
            <Input type="text" value={createSlug} onChange={(e) => setCreateSlug(e.target.value)} placeholder="mi-empresa" />
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Dominio</span>
            <Input type="text" value={createDomain} onChange={(e) => setCreateDomain(e.target.value)} placeholder="empresa.com" />
          </label>
          <div className="border-t border-border pt-3 mt-1">
            <p className="text-xs font-semibold uppercase text-muted-foreground mb-2">Usuario base del tenant</p>
          </div>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Email</span>
            <Input type="email" value={createUserEmail} onChange={(e) => setCreateUserEmail(e.target.value)} placeholder="admin@empresa.com" />
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Contraseña</span>
            <Input type="password" value={createUserPassword} onChange={(e) => setCreateUserPassword(e.target.value)} placeholder="Mínimo 8 caracteres" />
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Nombre completo (opcional)</span>
            <Input type="text" value={createUserFullName} onChange={(e) => setCreateUserFullName(e.target.value)} placeholder="Juan Pérez" />
          </label>
          {createError && <Alert title="Error">{createError}</Alert>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={() => setShowCreate(false)}>Cancelar</Button>
            <Button type="submit" size="sm" disabled={createMutation.isPending}>{createMutation.isPending ? "..." : "Crear"}</Button>
          </div>
        </form>
      </Dialog>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-muted-foreground">
              <th className="pb-2 pr-4">Nombre</th>
              <th className="pb-2 pr-4">Slug</th>
              <th className="pb-2 pr-4">Dominio</th>
              <th className="pb-2 pr-4">Estado</th>
              <th className="pb-2">Acciones</th>
            </tr>
          </thead>
          <tbody>
            {tenants.map((t) => (
              <tr key={t.id} className="border-b border-border/50">
                <td className="py-2 pr-4 text-foreground">{t.name}</td>
                <td className="py-2 pr-4 font-mono text-xs text-muted-foreground">{t.slug}</td>
                <td className="py-2 pr-4 text-foreground">{t.domain ?? "—"}</td>
                <td className="py-2 pr-4">
                  <span className={`inline-block rounded px-1.5 py-0.5 text-xs ${t.is_active ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>
                    {t.is_active ? "Activo" : "Inactivo"}
                  </span>
                </td>
                <td className="py-2">
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => { setSelectedTenant(t); setEditDomain(t.domain ?? ""); setEditError(null); }}
                    >
                      Dominio
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => { setShowUsers(t); setAssignUserId(""); setAssignError(null); }}
                    >
                      Usuarios
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
            {tenants.length === 0 && (
              <tr><td colSpan={5} className="py-4 text-center text-muted-foreground">No hay tenants</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <Dialog open={Boolean(selectedTenant)} title="Editar dominio" description={selectedTenant?.name} onClose={() => setSelectedTenant(null)}>
        <form className="space-y-3" onSubmit={handleEditDomain}>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Dominio (DMS)</span>
            <Input type="text" value={editDomain} onChange={(e) => setEditDomain(e.target.value)} placeholder="empresa.com" autoFocus />
          </label>
          {editError && <Alert title="Error">{editError}</Alert>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={() => setSelectedTenant(null)}>Cancelar</Button>
            <Button type="submit" size="sm" disabled={updateDomainMutation.isPending}>{updateDomainMutation.isPending ? "..." : "Guardar"}</Button>
          </div>
        </form>
      </Dialog>

      <Dialog open={Boolean(showUsers)} title={`Usuarios — ${showUsers?.name ?? ""}`} onClose={() => setShowUsers(null)}>
        <div className="space-y-3">
          <form onSubmit={handleCreateUser} className="space-y-2 rounded-md border border-border p-3">
            <p className="text-xs font-semibold uppercase text-muted-foreground">Crear usuario</p>
            <label className="block space-y-1 text-sm text-foreground">
              <span>Email</span>
              <Input type="email" value={newUserEmail} onChange={(e) => setNewUserEmail(e.target.value)} placeholder="usuario@empresa.com" />
            </label>
            <label className="block space-y-1 text-sm text-foreground">
              <span>Contraseña</span>
              <Input type="password" value={newUserPassword} onChange={(e) => setNewUserPassword(e.target.value)} placeholder="Mínimo 8 caracteres" />
            </label>
            <label className="block space-y-1 text-sm text-foreground">
              <span>Nombre completo (opcional)</span>
              <Input type="text" value={newUserFullName} onChange={(e) => setNewUserFullName(e.target.value)} placeholder="Juan Pérez" />
            </label>
            {newUserError && <Alert title="Error">{newUserError}</Alert>}
            <div className="flex justify-end">
              <Button type="submit" size="sm" disabled={createUserMutation.isPending}>{createUserMutation.isPending ? "..." : "Crear usuario"}</Button>
            </div>
          </form>

          {usersQuery.isLoading && <div className="text-sm text-muted-foreground">Cargando usuarios...</div>}

          {usersQuery.data?.users && (
            <div className="space-y-1">
              {usersQuery.data.users.map((u: TenantUserResponse) => (
                <div key={u.id} className="flex items-center justify-between rounded border border-border px-3 py-2">
                  <div>
                    <span className="text-sm text-foreground">{u.email}</span>
                    <span className="ml-2 text-xs text-muted-foreground">({u.full_name})</span>
                  </div>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => { setReassignUserId(u.id); setReassignTargetTenantId(""); setReassignError(null); }}
                  >
                    Quitar
                  </Button>
                </div>
              ))}
              {usersQuery.data.users.length === 0 && (
                <div className="text-sm text-muted-foreground">No hay usuarios asignados</div>
              )}
            </div>
          )}
        </div>
      </Dialog>

      <Dialog open={Boolean(reassignUserId)} title="Reasignar usuario" onClose={() => setReassignUserId(null)}>
        <div className="space-y-3">
          <form onSubmit={(e) => {
            e.preventDefault();
            setReassignError(null);
            if (!reassignTargetTenantId.trim()) { setReassignError("Target tenant ID requerido"); return; }
            removeMutation.mutate({ tenantId: showUsers!.id, userId: reassignUserId!, targetTenantId: reassignTargetTenantId });
          }}>
            <label className="block space-y-1 text-sm text-foreground">
              <span>Target Tenant ID</span>
              <Input type="text" value={reassignTargetTenantId} onChange={(e) => setReassignTargetTenantId(e.target.value)} placeholder="UUID del tenant destino" autoFocus />
            </label>
            {reassignError && <Alert title="Error">{reassignError}</Alert>}
            <div className="flex justify-end gap-2 mt-3">
              <Button type="button" variant="secondary" size="sm" onClick={() => setReassignUserId(null)}>Cancelar</Button>
              <Button type="submit" size="sm" disabled={removeMutation.isPending}>{removeMutation.isPending ? "..." : "Reasignar"}</Button>
            </div>
          </form>
        </div>
      </Dialog>
    </section>
  );
}
