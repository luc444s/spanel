import { useEffect, useState } from "react";
import { apiRequest } from "../api/client";
import type { CorePermissionRead, CoreRoleRead } from "../api/types";
import { Alert } from "../ui/alert";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Checkbox } from "../ui/checkbox";
import { Dialog } from "../ui/dialog";
import { Input } from "../ui/input";

type RoleForm = {
  name: string;
  permission_names: string[];
};

export function RolesView() {
  const [roles, setRoles] = useState<CoreRoleRead[]>([]);
  const [permissions, setPermissions] = useState<CorePermissionRead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<CoreRoleRead | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<RoleForm>({ name: "", permission_names: [] });

  const load = async () => {
    try {
      const [roleList, permissionList] = await Promise.all([
        apiRequest<CoreRoleRead[]>("/api/v1/core/roles"),
        apiRequest<CorePermissionRead[]>("/api/v1/core/permissions"),
      ]);
      setRoles(roleList);
      setPermissions(permissionList);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const run = async (roleId: string, action: "disable" | "enable") => {
    setBusy(roleId);
    setError(null);
    try {
      await apiRequest(`/api/v1/core/roles/${roleId}/${action}`, { method: "POST" });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const openCreate = () => {
    setForm({ name: "", permission_names: [] });
    setCreating(true);
  };

  const openEdit = (role: CoreRoleRead) => {
    setForm({ name: role.name, permission_names: role.permissions });
    setEditing(role);
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy("form");
    setError(null);
    try {
      if (creating) {
        await apiRequest("/api/v1/core/roles", {
          method: "POST",
          body: JSON.stringify(form),
        });
        setCreating(false);
      } else if (editing) {
        await apiRequest(`/api/v1/core/roles/${editing.id}`, {
          method: "PATCH",
          body: JSON.stringify(form),
        });
        setEditing(null);
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const togglePermission = (name: string) => {
    setForm((prev) => ({
      ...prev,
      permission_names: prev.permission_names.includes(name)
        ? prev.permission_names.filter((n) => n !== name)
        : [...prev.permission_names, name],
    }));
  };

  useEffect(() => {
    void load();
  }, []);

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div className="space-y-1">
            <CardTitle>Roles</CardTitle>
            <CardDescription>RBAC por tenant</CardDescription>
          </div>
          <Button onClick={openCreate}>Nuevo rol</Button>
        </CardHeader>
        <CardContent className="space-y-4">
          {error && <Alert title="Error">{error}</Alert>}
          {loading ? (
            <p className="text-sm text-muted-foreground">Cargando…</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th className="py-2 pr-4 font-medium">Nombre</th>
                    <th className="py-2 pr-4 font-medium">Permisos</th>
                    <th className="py-2 pr-4 font-medium">Estado</th>
                    <th className="py-2 font-medium">Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {roles.map((role) => (
                    <tr key={role.id} className="border-b border-border/50">
                      <td className="py-2 pr-4 font-medium">{role.name}</td>
                      <td className="py-2 pr-4 text-muted-foreground">
                        {role.permissions.length}
                      </td>
                      <td className="py-2 pr-4">
                        <Badge>{role.active ? "active" : "disabled"}</Badge>
                      </td>
                      <td className="py-2">
                        <div className="flex flex-wrap gap-2">
                          <Button variant="secondary" onClick={() => openEdit(role)}>
                            Editar
                          </Button>
                          {role.active ? (
                            <Button
                              variant="secondary"
                              disabled={busy === role.id}
                              onClick={() => void run(role.id, "disable")}
                            >
                              {busy === role.id ? "…" : "Deshabilitar"}
                            </Button>
                          ) : (
                            <Button
                              variant="secondary"
                              disabled={busy === role.id}
                              onClick={() => void run(role.id, "enable")}
                            >
                              {busy === role.id ? "…" : "Habilitar"}
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog
        open={creating || editing !== null}
        title={creating ? "Nuevo rol" : "Editar rol"}
        onClose={() => {
          setCreating(false);
          setEditing(null);
        }}
        actions={
          <div className="flex justify-end gap-3">
            <Button variant="secondary" type="button" onClick={() => setCreating(false)}>
              Cancelar
            </Button>
            <Button type="submit" form="role-form" disabled={busy === "form"}>
              Guardar
            </Button>
          </div>
        }
      >
        <form id="role-form" className="space-y-6" onSubmit={save}>
          {error && <Alert title="Error">{error}</Alert>}
          <label className="block space-y-2 text-sm text-foreground">
            <span>Nombre</span>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <fieldset className="space-y-2 text-sm">
            <legend className="text-foreground">Permisos</legend>
            <div className="grid max-h-64 gap-2 overflow-y-auto md:grid-cols-2">
              {permissions.map((permission) => (
                <label
                  key={permission.id}
                  className="flex items-center gap-2 text-sm text-foreground"
                >
                  <Checkbox
                    checked={form.permission_names.includes(permission.name)}
                    onChange={() => togglePermission(permission.name)}
                  />
                  <span className="truncate">{permission.name}</span>
                </label>
              ))}
            </div>
          </fieldset>
        </form>
      </Dialog>
    </>
  );
}
