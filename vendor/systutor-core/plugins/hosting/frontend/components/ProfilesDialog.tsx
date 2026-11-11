import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import {
  createHostingProfile,
  deleteHostingProfile,
  updateHostingProfile,
  hostingKeys,
  type StaticProfile,
} from "../api";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { toast } from "@systutor/shell/ui/toast";
import { Input } from "@systutor/shell/ui/input";
import { Select } from "@systutor/shell/ui/select";

import { useT } from "@/i18n/use-i18n";

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
  const t = useT();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<StaticProfile | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<StaticProfile | null>(null);
  const [confirmation, setConfirmation] = useState("");

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
            throw new Error(t("Each line must be VARIABLE=value"));
          }
          payload.env_json = env;
        }
        await updateHostingProfile(editing.id, payload);
      } else {
        const env = parseEnv(form.env_json);
        if (env === null) {
          throw new Error(t("Each line must be VARIABLE=value (or leave it empty)"));
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
      setForm(null);
      setEditing(null);
      await refresh();
    },
  });

  /**
   * A.SPEC 0043: the form closes at the submit instant (the manager stays
   * open); the mutation runs in background and toast.promise shows the
   * pending/final states. Errors keep the backend message (422 included).
   */
  function submitProfile(event: FormEvent) {
    event.preventDefault();
    setForm(null);
    setEditing(null);
    toast.promise(saveMutation.mutateAsync(), {
      loading: t("Saving profile…"),
      success: t("Profile saved"),
      error: (error: Error) => error.message || t("Could not save the profile"),
    });
  }

  const deleteMutation = useMutation({
    mutationFn: deleteHostingProfile,
    onSuccess: async () => {
      setDeleteTarget(null);
      setConfirmation("");
      await refresh();
    },
    onError: async () => await refresh(),
  });

  /**
   * A.SPEC 0043: the confirm dialog closes at the submit instant; the
   * DELETE runs in background and toast.promise reports the result with
   * the real backend message (409 in-use / builtin included).
   */
  function submitDeleteProfile() {
    if (!deleteTarget) return;
    const id = deleteTarget.id;
    setDeleteTarget(null);
    setConfirmation("");
    toast.promise(deleteMutation.mutateAsync(id), {
      loading: t("Deleting profile…"),
      success: t("Profile deleted"),
      error: (error: Error) => error.message || t("Could not delete the profile"),
    });
  }

  function openCreate() {
    setEditing(null);
    setForm(EMPTY_FORM);
  }

  function openEdit(profile: StaticProfile) {
    setEditing(profile);
    setForm(formOf(profile));
  }

  const isBuiltin = (profile: StaticProfile) => profile.id === "nginx";

  return (
    <>
      <Dialog
        open={open}
        title={t("Static site profiles")}
        description={t("Closed catalog curated by the platform admin")}
        maxWidthClassName="max-w-3xl"
        onClose={onClose}
      >
        <div className="space-y-3">
          {profiles.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("No profiles yet.")}</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground">
                  <th className="py-1">{t("ID")}</th>
                  <th className="py-1">{t("Name")}</th>
                  <th className="py-1">{t("Image")}</th>
                  <th className="py-1">{t("Port")}</th>
                  <th className="py-1">{t("Health")}</th>
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
                        {t("Edit")}
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
                          {t("Delete")}
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="text-xs text-muted-foreground">
            {t("* builtin: the image is editable; it is not deleted or renamed.")}
          </p>
          <div className="flex justify-end">
            <Button onClick={openCreate}>{t("+ New profile")}</Button>
          </div>
        </div>
      </Dialog>

      <Dialog
        open={form !== null}
        title={editing ? t("Edit profile") : t("New profile")}
        maxWidthClassName="max-w-2xl"
        onClose={() => {
          setForm(null);
          setEditing(null);
        }}
      >
        {form ? (
          <form
            className="space-y-3"
            onSubmit={submitProfile}
          >
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">
                {t("ID")} {editing ? t("(not changed)") : ""}
              </label>
              <Input
                required={!editing}
                disabled={Boolean(editing)}
                value={form.id}
                onChange={(event) => setForm({ ...form, id: event.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">{t("Name")}</label>
              <Input
                required
                disabled={editing !== null && isBuiltin(editing)}
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">{t("Image")}</label>
              <Input
                required
                value={form.image}
                onChange={(event) => setForm({ ...form, image: event.target.value })}
              />
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div className="col-span-2 space-y-1">
                <label className="text-xs text-muted-foreground">{t("Web root")}</label>
                <Input
                  required
                  value={form.web_root}
                  onChange={(event) => setForm({ ...form, web_root: event.target.value })}
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">{t("Port")}</label>
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
              <label className="text-xs text-muted-foreground">{t("Health check")}</label>
              <Select
                value={form.health_mode}
                onChange={(value) =>
                  setForm({ ...form, health_mode: value as FormState["health_mode"] })
                }
                options={[
                  { value: "marker", label: t("marker (own marker)") },
                  { value: "http_200", label: "http_200" },
                ]}
              />
            </div>
            <details className="rounded-md border border-border px-3 py-2">
              <summary className="text-xs text-muted-foreground">
                {t("Environment variables")}
              </summary>
              <p className="mt-1 text-xs text-muted-foreground">
                {t("One per line, as NAME=value in a new profile. After saving, the table only shows variable NAMES, never values.")}
              </p>
              <Input
                className="mt-2"
                value={form.env_json}
                onChange={(event) => setForm({ ...form, env_json: event.target.value })}
              />
            </details>
            <details className="rounded-md border border-border px-3 py-2">
              <summary className="text-xs text-muted-foreground">
                {t("Start command (sh -c)")}
              </summary>
              <p className="mt-1 text-xs text-muted-foreground">
                {t("The command waits for the LAST file in the seed before building/serving. Empty uses the image CMD.")}
              </p>
              <Input
                className="mt-2"
                value={form.command}
                onChange={(event) => setForm({ ...form, command: event.target.value })}
              />
            </details>
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setForm(null);
                  setEditing(null);
                }}
              >
                {t("Cancel")}
              </Button>
              <Button type="submit" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? t("Saving...") : t("Save")}
              </Button>
            </div>
          </form>
        ) : null}
      </Dialog>

      <Dialog
        open={deleteTarget !== null}
        title={t("Delete profile")}
        maxWidthClassName="max-w-md"
        onClose={() => {
          setDeleteTarget(null);
          setConfirmation("");
        }}
      >
        <p className="text-sm">
          {t('The profile "{id}" will be deleted. Type its id to confirm.', {
            id: deleteTarget?.id ?? "",
          })}
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
            {t("Cancel")}
          </Button>
          <Button
            className="border-destructive/60 text-destructive hover:bg-destructive/10"
            disabled={
              !deleteTarget || confirmation !== deleteTarget.id || deleteMutation.isPending
            }
            onClick={submitDeleteProfile}
          >
            {deleteMutation.isPending ? "..." : t("Delete")}
          </Button>
        </div>
      </Dialog>
    </>
  );
}
