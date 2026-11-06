import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  createHostingProfile,
  deleteHostingProfile,
  updateHostingProfile,
  hostingKeys,
  type StaticProfile,
} from "../api";
import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { toast } from "@systutor/shell/ui/toast";
import { Input } from "@systutor/shell/ui/input";
import { Select } from "@systutor/shell/ui/select";

type FormState = {
  id: string;
  name: string;
  image: string;
  web_root: string;
  container_port: string;
  health_mode: "marker" | "http_200";
  env_json: string;
  command: string;
};

const EMPTY_FORM: FormState = {
  id: "",
  name: "",
  image: "",
  web_root: "/",
  container_port: "80",
  health_mode: "marker",
  env_json: "",
  command: "",
};

function formOf(profile: StaticProfile): FormState {
  return {
    id: profile.id,
    name: profile.name,
    image: profile.image,
    web_root: profile.web_root,
    container_port: String(profile.container_port),
    health_mode: profile.health_mode,
    // Only NAMES travel back and forth: the backend never returns values.
    env_json: profile.env_keys.join("\n"),
    command: profile.command ?? "",
  };
}

/**
 * Parse NAME=value lines. Names-only lines (no `=`) mean "these variables
 * are already stored, unchanged": the values live in the backend table and
 * are never sent back and forth. `null` marks an impossible line.
 */
function parseEnv(raw: string): Record<string, string> | null {
  const entries: Record<string, string> = {};
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const separator = trimmed.indexOf("=");
    if (separator <= 0) return null;
    entries[trimmed.slice(0, separator).trim()] = trimmed.slice(separator + 1);
  }
  return entries;
}

/** Names-only lines (no `=`) mean the admin does not want to touch the env. */
function envIsUnchanged(raw: string): boolean {
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .every((line) => !line.includes("="));
}

type ProfilesDialogProps = {
  open: boolean;
  profiles: StaticProfile[];
  onClose: () => void;
};

/**
 * A.SPEC 0042: this dialog is only mounted for the absolute platform
 * control (`core.platform.admin` AND `hosting.profiles.manage`). The
 * builtin `nginx*` shows image editable, but no Delete and a readonly name.
 */
export function ProfilesDialog({ open, profiles, onClose }: ProfilesDialogProps) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<StaticProfile | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<StaticProfile | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [editError, setEditError] = useState<string | null>(null);

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: hostingKeys.profiles });
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!form) return;
      const lines = form.env_json
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      if (editing) {
        // Names-only lines mean "env unchanged"; NAME=value lines REPLACE
        // the set completely, values included. Values never come back.
        const payload: Parameters<typeof updateHostingProfile>[1] = {
          name: form.name,
          image: form.image,
          web_root: form.web_root,
          container_port: Number(form.container_port),
          health_mode: form.health_mode,
          command: form.command.trim() || null,
        };
        if (!envIsUnchanged(form.env_json)) {
          const env = parseEnv(form.env_json);
          if (env === null) {
            throw new Error("Cada línea debe ser VARIABLE=valor");
          }
          payload.env_json = env;
        }
        await updateHostingProfile(editing.id, payload);
      } else {
        const env = parseEnv(form.env_json);
        if (env === null) {
          throw new Error("Cada línea debe ser VARIABLE=valor (o no escribir nada)");
        }
        await createHostingProfile({
          id: form.id.trim(),
          name: form.name,
          image: form.image,
          web_root: form.web_root,
          container_port: Number(form.container_port),
          health_mode: form.health_mode,
          env_json: env,
          command: form.command.trim() || null,
        });
      }
    },
    onSuccess: async () => {
      toast.success(editing ? "Perfil actualizado" : "Perfil creado");
      setForm(null);
      setEditing(null);
      setEditError(null);
      await refresh();
    },
    onError: (error: Error) => setEditError(error.message),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteHostingProfile,
    onSuccess: async () => {
      toast.success("Perfil eliminado");
      setDeleteTarget(null);
      setConfirmation("");
      await refresh();
    },
    onError: async (error: Error) => {
      setDeleteTarget(null);
      setConfirmation("");
      toast.error(error.message);
      await refresh();
    },
  });

  function openCreate() {
    setEditing(null);
    setForm(EMPTY_FORM);
    setEditError(null);
  }

  function openEdit(profile: StaticProfile) {
    setEditing(profile);
    setForm(formOf(profile));
    setEditError(null);
  }

  const isBuiltin = (profile: StaticProfile) => profile.id === "nginx";

  return (
    <>
      <Dialog
        open={open}
        title="Perfiles de sitio estático"
        description="Catálogo cerrado y curado por el admin de plataforma"
        maxWidthClassName="max-w-3xl"
        onClose={onClose}
      >
        <div className="space-y-3">
          {profiles.length === 0 ? (
            <p className="text-sm text-muted-foreground">No hay perfiles todavía.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground">
                  <th className="py-1">ID</th>
                  <th className="py-1">Nombre</th>
                  <th className="py-1">Imagen</th>
                  <th className="py-1">Pu</th>
                  <th className="py-1">Health</th>
                  <th className="py-1" />
                </tr>
              </thead>
              <tbody>
                {profiles.map((profile) => (
                  <tr key={profile.id} className="border-t border-border">
                    <td className="py-1 font-mono text-xs">
                      {isBuiltin(profile) ? `${profile.id}*` : profile.id}
                    </td>
                    <td className="py-1">{profile.name}</td>
                    <td className="py-1 font-mono text-xs">{profile.image}</td>
                    <td className="py-1">{profile.container_port}</td>
                    <td className="py-1">{profile.health_mode}</td>
                    <td className="py-1 text-right">
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => openEdit(profile)}
                      >
                        Editar
                      </Button>
                      {!isBuiltin(profile) ? (
                        <Button
                          variant="secondary"
                          size="sm"
                          className="ml-2 border-destructive/60 text-destructive hover:bg-destructive/10"
                          onClick={() => {
                            setDeleteTarget(profile);
                            setConfirmation("");
                          }}
                        >
                          Eliminar
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="text-xs text-muted-foreground">
            * builtin: la imagen es editable; no se borra ni renombra.
          </p>
          <div className="flex justify-end">
            <Button onClick={openCreate}>+ Nuevo perfil</Button>
          </div>
        </div>
      </Dialog>

      <Dialog
        open={form !== null}
        title={editing ? "Editar perfil" : "Nuevo perfil"}
        maxWidthClassName="max-w-2xl"
        onClose={() => {
          setForm(null);
          setEditing(null);
        }}
      >
        {form ? (
          <form
            className="space-y-3"
            onSubmit={(event) => {
              event.preventDefault();
              saveMutation.mutate();
            }}
          >
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">
                ID {editing ? "(no se cambia)" : ""}
              </label>
              <Input
                required={!editing}
                disabled={Boolean(editing)}
                value={form.id}
                onChange={(event) => setForm({ ...form, id: event.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Nombre</label>
              <Input
                required
                disabled={editing !== null && isBuiltin(editing)}
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Imagen</label>
              <Input
                required
                value={form.image}
                onChange={(event) => setForm({ ...form, image: event.target.value })}
              />
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div className="col-span-2 space-y-1">
                <label className="text-xs text-muted-foreground">Web root</label>
                <Input
                  required
                  value={form.web_root}
                  onChange={(event) => setForm({ ...form, web_root: event.target.value })}
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Puerto</label>
                <Input
                  required
                  inputMode="numeric"
                  value={form.container_port}
                  onChange={(event) =>
                    setForm({ ...form, container_port: event.target.value })
                  }
                />
              </div>
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Health check</label>
              <Select
                value={form.health_mode}
                onChange={(value) =>
                  setForm({ ...form, health_mode: value as FormState["health_mode"] })
                }
                options={[
                  { value: "marker", label: "marker (marcador propio)" },
                  { value: "http_200", label: "http_200" },
                ]}
              />
            </div>
            <details className="rounded-md border border-border px-3 py-2">
              <summary className="text-xs text-muted-foreground">
                Variables de entorno
              </summary>
              <p className="mt-1 text-xs text-muted-foreground">
                Una por línea, como NOMBRE=valor en un perfil nuevo. Tras guardar, la
                tabla solo muestra NOMBRES de variables, nunca valores.
              </p>
              <Input
                className="mt-2"
                value={form.env_json}
                onChange={(event) => setForm({ ...form, env_json: event.target.value })}
              />
            </details>
            <details className="rounded-md border border-border px-3 py-2">
              <summary className="text-xs text-muted-foreground">
                Comando de arranque (sh -c)
              </summary>
              <p className="mt-1 text-xs text-muted-foreground">
                El comando espera al ÚLTIMO archivo del seed antes de compilar/servir.
                Vacío usa el CMD de la imagen.
              </p>
              <Input
                className="mt-2"
                value={form.command}
                onChange={(event) => setForm({ ...form, command: event.target.value })}
              />
            </details>
            {editError ? <Alert title="Error">{editError}</Alert> : null}
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setForm(null);
                  setEditing(null);
                }}
              >
                Cancelar
              </Button>
              <Button type="submit" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? "Guardando..." : "Guardar"}
              </Button>
            </div>
          </form>
        ) : null}
      </Dialog>

      <Dialog
        open={deleteTarget !== null}
        title="Eliminar perfil"
        maxWidthClassName="max-w-md"
        onClose={() => {
          setDeleteTarget(null);
          setConfirmation("");
        }}
      >
        <p className="text-sm">
          Se eliminará el perfil &quot;{deleteTarget?.id}&quot;. Escriba su id para confirmar.
        </p>
        <Input
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
        />
        <div className="mt-3 flex justify-end gap-2">
          <Button
            variant="secondary"
            onClick={() => {
              setDeleteTarget(null);
              setConfirmation("");
            }}
          >
            Cancelar
          </Button>
          <Button
            className="border-destructive/60 text-destructive hover:bg-destructive/10"
            disabled={
              !deleteTarget || confirmation !== deleteTarget.id || deleteMutation.isPending
            }
            onClick={() => {
              if (deleteTarget) deleteMutation.mutate(deleteTarget.id);
            }}
          >
            {deleteMutation.isPending ? "..." : "Eliminar"}
          </Button>
        </div>
      </Dialog>
    </>
  );
}
