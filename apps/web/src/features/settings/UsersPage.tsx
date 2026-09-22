import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useMemo, useState } from "react";

import {
  CoreBranch,
  CoreRole,
  CoreUser,
  coreManagementKeys,
  createCoreUser,
  disableCoreUser,
  enableCoreUser,
  invalidateCoreManagementKey,
  listCoreBranches,
  listCoreRoles,
  listCoreUsers,
  updateCoreUser,
} from "../core-management/api";
import { useAuthStore } from "../auth/store";
import { Alert } from "@systutor/shell/ui/alert";
import { Badge } from "@systutor/shell/ui/badge";
import { Button } from "@systutor/shell/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@systutor/shell/ui/card";
import { DataTable } from "@systutor/shell/ui/data-table";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { Select } from "@systutor/shell/ui/select";

type UserFormState = {
  id?: string;
  name: string;
  email: string;
  password: string;
  branch_id: string;
  role_ids: string[];
};

const EMPTY_USER_FORM: UserFormState = {
  name: "",
  email: "",
  password: "",
  branch_id: "",
  role_ids: [],
};

function getAssignableRoles(roles: CoreRole[]) {
  return roles.filter((role) => role.active);
}

export function UsersPage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((state) => state.permissions);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [formState, setFormState] = useState<UserFormState>(EMPTY_USER_FORM);
  const [formError, setFormError] = useState<string | null>(null);

  const canCreate = permissions.includes("core.users.create");
  const canUpdate = permissions.includes("core.users.update");
  const canDisable = permissions.includes("core.users.disable");
  const canEditUsers = canCreate || canUpdate;

  const usersQuery = useQuery({ queryKey: [...coreManagementKeys.users], queryFn: listCoreUsers });
  const rolesQuery = useQuery({
    queryKey: [...coreManagementKeys.roles],
    queryFn: listCoreRoles,
    enabled: canEditUsers,
  });
  const branchesQuery = useQuery({
    queryKey: [...coreManagementKeys.branches],
    queryFn: listCoreBranches,
    enabled: canEditUsers,
  });

  const saveUserMutation = useMutation({
    mutationFn: async (payload: UserFormState) => {
      if (payload.id) {
        return updateCoreUser(payload.id, {
          name: payload.name,
          email: payload.email,
          password: payload.password || undefined,
          branch_id: payload.branch_id || null,
          role_ids: payload.role_ids,
        });
      }

      return createCoreUser({
        name: payload.name,
        email: payload.email,
        password: payload.password,
        branch_id: payload.branch_id || null,
        role_ids: payload.role_ids,
      });
    },
    onSuccess: async () => {
      await invalidateCoreManagementKey(queryClient, coreManagementKeys.users);
      setIsDialogOpen(false);
      setFormState(EMPTY_USER_FORM);
      setFormError(null);
    },
  });

  const toggleUserMutation = useMutation({
    mutationFn: async ({ userId, active }: { userId: string; active: boolean }) => {
      return active ? disableCoreUser(userId) : enableCoreUser(userId);
    },
    onSuccess: async () => {
      await invalidateCoreManagementKey(queryClient, coreManagementKeys.users);
    },
  });

  const rolesByName = useMemo(() => {
    const entries = (rolesQuery.data ?? []).map((role) => [role.name, role.id] as const);
    return new Map(entries);
  }, [rolesQuery.data]);

  function openCreateDialog() {
    setFormState(EMPTY_USER_FORM);
    setFormError(null);
    setIsDialogOpen(true);
  }

  function openEditDialog(user: CoreUser) {
    setFormState({
      id: user.id,
      name: user.name,
      email: user.email,
      password: "",
      branch_id: user.branch_id ?? "",
      role_ids: user.roles.map((roleName) => rolesByName.get(roleName) ?? "").filter(Boolean),
    });
    setFormError(null);
    setIsDialogOpen(true);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    if (!formState.id && !formState.password) {
      setFormError("Password es obligatorio al crear el usuario.");
      return;
    }

    try {
      await saveUserMutation.mutateAsync(formState);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "No se pudo guardar el usuario.");
    }
  }

  return (
    <UsersPageContent
      users={usersQuery.data ?? []}
      roles={getAssignableRoles(rolesQuery.data ?? [])}
      branches={branchesQuery.data ?? []}
      canCreate={canCreate}
      canUpdate={canUpdate}
      canDisable={canDisable}
      isDialogOpen={isDialogOpen}
      formState={formState}
      formError={formError}
      isSaving={saveUserMutation.isPending}
      isToggling={toggleUserMutation.isPending}
      hasError={Boolean(usersQuery.error || rolesQuery.error || branchesQuery.error)}
      onCreate={openCreateDialog}
      onEdit={openEditDialog}
      onCloseDialog={() => setIsDialogOpen(false)}
      onSubmit={handleSubmit}
      onFieldChange={(value) => setFormState((current) => ({ ...current, ...value }))}
      onToggleUser={(user) => toggleUserMutation.mutate({ userId: user.id, active: user.active })}
    />
  );
}

type UsersPageContentProps = {
  users: CoreUser[];
  roles: CoreRole[];
  branches: CoreBranch[];
  canCreate: boolean;
  canUpdate: boolean;
  canDisable: boolean;
  isDialogOpen: boolean;
  formState: UserFormState;
  formError: string | null;
  isSaving: boolean;
  isToggling: boolean;
  hasError: boolean;
  onCreate: () => void;
  onEdit: (user: CoreUser) => void;
  onCloseDialog: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onFieldChange: (value: Partial<UserFormState>) => void;
  onToggleUser: (user: CoreUser) => void;
};

export function UsersPageContent({
  users,
  roles,
  branches,
  canCreate,
  canUpdate,
  canDisable,
  isDialogOpen,
  formState,
  formError,
  isSaving,
  isToggling,
  hasError,
  onCreate,
  onEdit,
  onCloseDialog,
  onSubmit,
  onFieldChange,
  onToggleUser,
}: UsersPageContentProps) {
  return (
    <section className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold text-foreground">Usuarios</h1>
          <p className="text-sm text-muted-foreground">
            Administracion tenant-aware de usuarios, branch y roles efectivos del core.
          </p>
        </div>
        {canCreate ? <Button onClick={onCreate}>Crear usuario</Button> : null}
      </div>

      {hasError ? (
        <Alert title="No se pudieron cargar los usuarios">
          Revisa permisos o conectividad con el backend.
        </Alert>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Usuarios</CardTitle>
          <CardDescription>Lista mínima operativa del tenant actual.</CardDescription>
        </CardHeader>
        <CardContent>
          <DataTable
            columns={[
              { key: "name", header: "Nombre", render: (user) => user.name },
              { key: "email", header: "Correo", render: (user) => user.email },
              {
                key: "branch",
                header: "Sucursal",
                render: (user) => branches.find((branch) => branch.id === user.branch_id)?.name ?? "-",
              },
              {
                key: "roles",
                header: "Roles",
                render: (user) => (
                  <div className="flex flex-wrap gap-2">
                    {user.roles.length > 0 ? user.roles.map((role) => <Badge key={role}>{role}</Badge>) : "-"}
                  </div>
                ),
              },
              {
                key: "status",
                header: "Estado",
                render: (user) => <StatusBadge active={user.active} />,
              },
              {
                key: "actions",
                header: "Acciones",
                className: "w-56",
                render: (user) => (
                  <div className="flex flex-wrap gap-2">
                    {canUpdate ? (
                      <Button variant="secondary" onClick={() => onEdit(user)}>
                        Editar
                      </Button>
                    ) : null}
                    {canDisable ? (
                      <Button variant="secondary" disabled={isToggling} onClick={() => onToggleUser(user)}>
                        {user.active ? "Desactivar" : "Activar"}
                      </Button>
                    ) : null}
                  </div>
                ),
              },
            ]}
            rows={users}
            rowKey={(user) => user.id}
            emptyMessage="No hay usuarios en este tenant."
          />
        </CardContent>
      </Card>

      <Dialog
        open={isDialogOpen}
        title={formState.id ? "Editar usuario" : "Crear usuario"}
        description="Formulario mínimo del core management."
        onClose={onCloseDialog}
      >
        <form className="space-y-4" onSubmit={onSubmit}>
          <label className="block space-y-2 text-sm text-foreground">
            <span>Nombre</span>
            <Input value={formState.name} onChange={(event) => onFieldChange({ name: event.target.value })} />
          </label>

          <label className="block space-y-2 text-sm text-foreground">
            <span>Correo</span>
            <Input type="email" value={formState.email} onChange={(event) => onFieldChange({ email: event.target.value })} />
          </label>

          <label className="block space-y-2 text-sm text-foreground">
            <span>Contraseña</span>
            <Input
              type="password"
              value={formState.password}
              onChange={(event) => onFieldChange({ password: event.target.value })}
            />
          </label>

          <label className="block space-y-2 text-sm text-foreground">
            <span>Sucursal</span>
            <Select
              value={formState.branch_id || ""}
              onChange={(value) => onFieldChange({ branch_id: value })}
              options={[
                { value: "", label: "Sin sucursal" },
                ...branches.map((branch) => ({ value: branch.id, label: `${branch.code} - ${branch.name}` })),
              ]}
            />
          </label>

          <fieldset className="space-y-2">
            <legend className="text-sm text-foreground">Roles</legend>
            <div className="grid gap-2 rounded-md border border-border bg-muted/60 p-3 sm:grid-cols-2 max-h-64 overflow-y-auto">
              {roles.map((role) => {
                const checked = formState.role_ids.includes(role.id);
                return (
                  <label key={role.id} className="flex items-center gap-2 text-sm text-foreground">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(event) => {
                        onFieldChange({
                          role_ids: event.target.checked
                            ? [...formState.role_ids, role.id]
                            : formState.role_ids.filter((item) => item !== role.id),
                        });
                      }}
                    />
                    <span>{role.name}</span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          {formError ? <Alert title="No se pudo guardar el usuario">{formError}</Alert> : null}

          <div className="flex justify-end gap-3">
            <Button type="button" variant="secondary" onClick={onCloseDialog}>
              Cancelar
            </Button>
            <Button type="submit" disabled={isSaving}>
              {formState.id ? "Guardar cambios" : "Crear usuario"}
            </Button>
          </div>
        </form>
      </Dialog>
    </section>
  );
}

function StatusBadge({ active }: { active: boolean }) {
  return <Badge>{active ? "Activo" : "Inactivo"}</Badge>;
}
