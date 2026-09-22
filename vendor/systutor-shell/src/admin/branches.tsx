import { useEffect, useState } from "react";
import { apiRequest } from "../api/client";
import type { CoreBranchRead } from "../api/types";
import { Alert } from "../ui/alert";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Dialog } from "../ui/dialog";
import { Input } from "../ui/input";

type BranchForm = {
  name: string;
  code: string;
};

export function BranchesView() {
  const [branches, setBranches] = useState<CoreBranchRead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<CoreBranchRead | null>(null);
  const [form, setForm] = useState<BranchForm>({ name: "", code: "" });

  const load = async () => {
    try {
      setBranches(await apiRequest<CoreBranchRead[]>("/api/v1/core/branches"));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const run = async (branchId: string, action: "disable" | "enable") => {
    setBusy(branchId);
    setError(null);
    try {
      await apiRequest(`/api/v1/core/branches/${branchId}/${action}`, { method: "POST" });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const openCreate = () => {
    setForm({ name: "", code: "" });
    setCreating(true);
  };

  const openEdit = (branch: CoreBranchRead) => {
    setForm({ name: branch.name, code: branch.code });
    setEditing(branch);
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy("form");
    setError(null);
    try {
      if (creating) {
        await apiRequest("/api/v1/core/branches", {
          method: "POST",
          body: JSON.stringify(form),
        });
        setCreating(false);
      } else if (editing) {
        await apiRequest(`/api/v1/core/branches/${editing.id}`, {
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

  useEffect(() => {
    void load();
  }, []);

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div className="space-y-1">
            <CardTitle>Branches</CardTitle>
            <CardDescription>Sucursales del tenant activo</CardDescription>
          </div>
          <Button onClick={openCreate}>Nuevo branch</Button>
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
                    <th className="py-2 pr-4 font-medium">Código</th>
                    <th className="py-2 pr-4 font-medium">Estado</th>
                    <th className="py-2 font-medium">Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {branches.map((branch) => (
                    <tr key={branch.id} className="border-b border-border/50">
                      <td className="py-2 pr-4 font-medium">{branch.name}</td>
                      <td className="py-2 pr-4 text-muted-foreground">{branch.code}</td>
                      <td className="py-2 pr-4">
                        <Badge>{branch.active ? "active" : "disabled"}</Badge>
                      </td>
                      <td className="py-2">
                        <div className="flex flex-wrap gap-2">
                          <Button variant="secondary" onClick={() => openEdit(branch)}>
                            Editar
                          </Button>
                          {branch.active ? (
                            <Button
                              variant="secondary"
                              disabled={busy === branch.id}
                              onClick={() => void run(branch.id, "disable")}
                            >
                              {busy === branch.id ? "…" : "Deshabilitar"}
                            </Button>
                          ) : (
                            <Button
                              variant="secondary"
                              disabled={busy === branch.id}
                              onClick={() => void run(branch.id, "enable")}
                            >
                              {busy === branch.id ? "…" : "Habilitar"}
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
        title={creating ? "Nuevo branch" : "Editar branch"}
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
            <Button type="submit" form="branch-form" disabled={busy === "form"}>
              Guardar
            </Button>
          </div>
        }
      >
        <form id="branch-form" className="space-y-6" onSubmit={save}>
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
              <span>Código</span>
              <Input
                value={form.code}
                onChange={(e) => setForm({ ...form, code: e.target.value })}
              />
            </label>
          </div>
        </form>
      </Dialog>
    </>
  );
}
