import { useEffect, useState } from "react";
import { apiRequest } from "../api/client";
import type { CoreRoleRead, CoreUserCategoryRead, CoreUserRead } from "../api/types";
import { Alert } from "../ui/alert";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Checkbox } from "../ui/checkbox";
import { Dialog } from "../ui/dialog";
import { Input } from "../ui/input";
import { Select } from "../ui/select";

type UserForm = {
  name: string;
  email: string;
  password: string;
  category: string | null;
  role_ids: string[];
};

const EMPTY_FORM: UserForm = { name: "", email: "", password: "", category: null, role_ids: [] };

export function UsersView() {
  const [users, setUsers] = useState<CoreUserRead[]>([]);
  const [roles, setRoles] = useState<CoreRoleRead[]>([]);
  const [categories, setCategories] = useState<CoreUserCategoryRead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<CoreUserRead | null>(null);
  const [form, setForm] = useState<UserForm>(EMPTY_FORM);

  const load = async () => {
    try {
      const [userList, roleList, categoryList] = await Promise.all([
        apiRequest<CoreUserRead[]>("/api/v1/core/users"),
        apiRequest<CoreRoleRead[]>("/api/v1/core/roles"),
        apiRequest<CoreUserCategoryRead[]>("/api/v1/core/users/categories"),
      ]);
      setUsers(userList);
      setRoles(roleList);
      setCategories(categoryList);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const run = async (userId: string, action: "disable" | "enable") => {
    setBusy(userId);
    setError(null);
    try {
      await apiRequest(`/api/v1/core/users/${userId}/${action}`, { method: "POST" });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const openCreate = () => {
    setForm(EMPTY_FORM);
    setCreating(true);
  };

  const openEdit = (user: CoreUserRead) => {
    setForm({
      name: user.name,
      email: user.email,
      password: "",
      category: user.category,
      role_ids: user.roles,
    });
    setEditing(user);
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy("form");
    setError(null);
    try {
      if (creating) {
        const payload = {
          name: form.name,
          email: form.email,
          password: form.password,
          category: form.category,
          role_ids: form.role_ids,
        };
        await apiRequest("/api/v1/core/users", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        setCreating(false);
      } else if (editing) {
        const payload: Record<string, unknown> = {
          name: form.name,
          email: form.email,
          category: form.category,
          role_ids: form.role_ids,
        };
        if (form.password) payload.password = form.password;
        await apiRequest(`/api/v1/core/users/${editing.id}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
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

  const toggleRole = (roleId: string) => {
    setForm((prev) => ({
      ...prev,
      role_ids: prev.role_ids.includes(roleId)
        ? prev.role_ids.filter((id) => id !== roleId)
        : [...prev.role_ids, roleId],
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
            <CardTitle>Usuarios</CardTitle>
            <CardDescription>Cuentas del tenant</CardDescription>
          </div>
          <Button onClick={openCreate}>Nuevo usuario</Button>
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
                    <th className="py-2 pr-4 font-medium">Email</th>
                    <th className="py-2 pr-4 font-medium">Categoría</th>
                    <th className="py-2 pr-4 font-medium">Roles</th>
                    <th className="py-2 pr-4 font-medium">Estado</th>
                    <th className="py-2 font-medium">Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => (
                    <tr key={user.id} className="border-b border-border/50">
                      <td className="py-2 pr-4 font-medium">{user.name}</td>
                      <td className="py-2 pr-4 text-muted-foreground">{user.email}</td>
                      <td className="py-2 pr-4 text-muted-foreground">
                        {user.category ?? "—"}
                      </td>
                      <td className="py-2 pr-4 text-muted-foreground">
                        {user.roles.length}
                      </td>
                      <td className="py-2 pr-4">
                        <Badge>{user.active ? "active" : "disabled"}</Badge>
                      </td>
                      <td className="py-2">
                        <div className="flex flex-wrap gap-2">
                          <Button variant="secondary" onClick={() => openEdit(user)}>
                            Editar
                          </Button>
                          {user.active ? (
                            <Button
                              variant="secondary"
                              disabled={busy === user.id}
                              onClick={() => void run(user.id, "disable")}
                            >
                              {busy === user.id ? "…" : "Deshabilitar"}
                            </Button>
                          ) : (
                            <Button
                              variant="secondary"
                              disabled={busy === user.id}
                              onClick={() => void run(user.id, "enable")}
                            >
                              {busy === user.id ? "…" : "Habilitar"}
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
        title={creating ? "Nuevo usuario" : "Editar usuario"}
        onClose={() => {
          setCreating(false);
          setEditing(null);
        }}
        actions={
          <div className="flex justify-end gap-3">
            <Button
              variant="secondary"
              type="button"
              onClick={() => {
                setCreating(false);
                setEditing(null);
              }}
            >
              Cancelar
            </Button>
            <Button type="submit" form="user-form" disabled={busy === "form"}>
              Guardar
            </Button>
          </div>
        }
      >
        <form id="user-form" className="space-y-6" onSubmit={save}>
          {error && <Alert title="Error">{error}</Alert>}
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block space-y-2 text-sm text-foreground">
              <span>Nombre</span>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </label>
            <label className="block space-y-2 text-sm text-foreground">
              <span>Email</span>
              <Input
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </label>
          </div>
          <label className="block space-y-2 text-sm text-foreground">
            <span>{creating ? "Password" : "Nuevo password (opcional)"}</span>
            <Input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              autoComplete="new-password"
            />
          </label>
          <label className="block space-y-2 text-sm text-foreground">
            <span>Categoría</span>
            <Select
              value={form.category ?? ""}
              onChange={(value) => setForm({ ...form, category: value || null })}
              options={categories.map((c) => ({ value: c.value, label: c.label }))}
              placeholder="Sin categoría"
            />
          </label>
          <fieldset className="space-y-2 text-sm">
            <legend className="text-foreground">Roles</legend>
            <div className="grid max-h-64 gap-2 overflow-y-auto md:grid-cols-2">
              {roles.map((role) => (
                <label
                  key={role.id}
                  className="flex items-center gap-2 text-sm text-foreground"
                >
                  <Checkbox
                    checked={form.role_ids.includes(role.id)}
                    onChange={() => toggleRole(role.id)}
                  />
                  <span className="truncate">{role.name}</span>
                </label>
              ))}
            </div>
          </fieldset>
        </form>
      </Dialog>
    </>
  );
}
