import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useMemo, useState } from "react";

import {
  CorePermission,
  CoreRole,
  coreManagementKeys,
  createCoreRole,
  disableCoreRole,
  enableCoreRole,
  invalidateCoreManagementKey,
  listCorePermissions,
  listCoreRoles,
  updateCoreRole,
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

type RoleFormState = {
  id?: string;
  name: string;
  permission_names: string[];
};

const EMPTY_ROLE_FORM: RoleFormState = { name: "", permission_names: [] };

export function RolesPage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((state) => state.permissions);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [formState, setFormState] = useState<RoleFormState>(EMPTY_ROLE_FORM);
  const [formError, setFormError] = useState<string | null>(null);

  const rolesQuery = useQuery({ queryKey: [...coreManagementKeys.roles], queryFn: listCoreRoles });
  const permissionsQuery = useQuery({
    queryKey: [...coreManagementKeys.permissions],
    queryFn: listCorePermissions,
    enabled: permissions.includes("core.roles.manage") || permissions.includes("core.permission.manage"),
  });

  const canManage = permissions.includes("core.roles.manage");

  const saveRoleMutation = useMutation({
    mutationFn: async (payload: RoleFormState) => {
      if (payload.id) {
        return updateCoreRole(payload.id, payload);
      }
      return createCoreRole(payload);
    },
    onSuccess: async () => {
      await invalidateCoreManagementKey(queryClient, coreManagementKeys.roles);
      setIsDialogOpen(false);
      setFormState(EMPTY_ROLE_FORM);
      setFormError(null);
    },
  });

  const toggleRoleMutation = useMutation({
    mutationFn: async ({ roleId, active }: { roleId: string; active: boolean }) => {
      return active ? disableCoreRole(roleId) : enableCoreRole(roleId);
    },
    onSuccess: async () => {
      await invalidateCoreManagementKey(queryClient, coreManagementKeys.roles);
    },
  });

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    try {
      await saveRoleMutation.mutateAsync(formState);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "No se pudo guardar el rol.");
    }
  }

  return (
    <RolesPageContent
      roles={rolesQuery.data ?? []}
      permissions={permissionsQuery.data ?? []}
      canManage={canManage}
      hasError={Boolean(rolesQuery.error || permissionsQuery.error)}
      isDialogOpen={isDialogOpen}
      formState={formState}
      formError={formError}
      isSaving={saveRoleMutation.isPending}
      onCreate={() => {
        setFormState(EMPTY_ROLE_FORM);
        setFormError(null);
        setIsDialogOpen(true);
      }}
      onEdit={(role) => {
        setFormState({ id: role.id, name: role.name, permission_names: role.permissions });
        setFormError(null);
        setIsDialogOpen(true);
      }}
      onCloseDialog={() => setIsDialogOpen(false)}
      onSubmit={handleSubmit}
      onFieldChange={(value) => setFormState((current) => ({ ...current, ...value }))}
      onToggleRole={(role) => toggleRoleMutation.mutate({ roleId: role.id, active: role.active })}
    />
  );
}

type RolesPageContentProps = {
  roles: CoreRole[];
  permissions: CorePermission[];
  canManage: boolean;
  hasError: boolean;
  isDialogOpen: boolean;
  formState: RoleFormState;
  formError: string | null;
  isSaving: boolean;
  onCreate: () => void;
  onEdit: (role: CoreRole) => void;
  onCloseDialog: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onFieldChange: (value: Partial<RoleFormState>) => void;
  onToggleRole: (role: CoreRole) => void;
};

export function RolesPageContent({
  roles,
  permissions,
  canManage,
  hasError,
  isDialogOpen,
  formState,
  formError,
  isSaving,
  onCreate,
  onEdit,
  onCloseDialog,
  onSubmit,
  onFieldChange,
  onToggleRole,
}: RolesPageContentProps) {
  const [filterModule, setFilterModule] = useState("_all");

  const modules = useMemo(() => {
    const unique = new Set(permissions.map((p) => p.name.split(".")[0]));
    return ["_all", ...Array.from(unique).sort()];
  }, [permissions]);

  const filteredPermissions = useMemo(() => {
    if (filterModule === "_all") return permissions;
    return permissions.filter((p) => p.name.startsWith(filterModule + "."));
  }, [permissions, filterModule]);

  return (
    <section className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold text-foreground">Roles</h1>
          <p className="text-sm text-muted-foreground">Administracion tenant-aware de roles y permisos efectivos.</p>
        </div>
        {canManage ? <Button onClick={onCreate}>Crear rol</Button> : null}
      </div>

      {hasError ? (
        <Alert title="No se pudieron cargar los roles">Revisa permisos o conectividad.</Alert>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Roles</CardTitle>
          <CardDescription>Roles tenant-scoped con catálogo global de permisos.</CardDescription>
        </CardHeader>
        <CardContent>
          <DataTable
            columns={[
              { key: "name", header: "Rol", render: (role) => role.name },
              {
                key: "count",
                header: "Permisos",
                render: (role) => String(role.permissions.length),
              },
              { key: "status", header: "Estado", render: (role) => <Badge>{role.active ? "Activo" : "Inactivo"}</Badge> },
              {
                key: "actions",
                header: "Acciones",
                className: "w-56",
                render: (role) => (
                  <div className="flex flex-wrap gap-2">
                    {canManage ? (
                      <Button variant="secondary" onClick={() => onEdit(role)}>
                        Editar
                      </Button>
                    ) : null}
                    {canManage ? (
                      <Button variant="secondary" onClick={() => onToggleRole(role)}>
                        {role.active ? "Desactivar" : "Activar"}
                      </Button>
                    ) : null}
                  </div>
                ),
              },
            ]}
            rows={roles}
            rowKey={(role) => role.id}
            emptyMessage="No hay roles definidos para este tenant."
          />
        </CardContent>
      </Card>

      <Dialog
        open={isDialogOpen}
        title={formState.id ? "Editar rol" : "Crear rol"}
        description="Formulario mínimo del core management."
        onClose={onCloseDialog}
      >
        <form className="space-y-4" onSubmit={onSubmit}>
          <label className="block space-y-2 text-sm text-foreground">
            <span>Nombre</span>
            <Input value={formState.name} onChange={(event) => onFieldChange({ name: event.target.value })} />
          </label>

          <fieldset className="space-y-2">
            <legend className="text-sm text-foreground">Permisos</legend>
            <Select
              value={filterModule}
              onChange={setFilterModule}
              options={modules.map((m) => ({
                value: m,
                label: m === "_all" ? "Todos los módulos" : m,
              }))}
            />
            <div className="grid max-h-80 gap-2 overflow-y-auto rounded-md border border-border bg-muted/60 p-3 sm:grid-cols-2">
              {filteredPermissions.map((permission) => {
                const checked = formState.permission_names.includes(permission.name);
                return (
                  <label key={permission.id} className="flex items-center gap-2 text-sm text-foreground">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(event) => {
                        onFieldChange({
                          permission_names: event.target.checked
                            ? [...formState.permission_names, permission.name]
                            : formState.permission_names.filter((item) => item !== permission.name),
                        });
                      }}
                    />
                    <span>{permission.name}</span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          {formError ? <Alert title="No se pudo guardar el rol">{formError}</Alert> : null}

          <div className="flex justify-end gap-3">
            <Button type="button" variant="secondary" onClick={onCloseDialog}>
              Cancelar
            </Button>
            <Button type="submit" disabled={isSaving}>
              {formState.id ? "Guardar cambios" : "Crear rol"}
            </Button>
          </div>
        </form>
      </Dialog>
    </section>
  );
}
