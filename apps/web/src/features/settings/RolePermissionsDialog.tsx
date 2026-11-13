import { FormEvent, useEffect, useMemo, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Select } from "@systutor/shell/ui/select";
import { useT } from "@/i18n/use-i18n";

import type { CorePermission, CoreRole } from "../core-management/api";

export function RolePermissionsDialog({
  role,
  permissions,
  memberCount,
  saving,
  error,
  onClose,
  onSave,
}: {
  role: CoreRole | null;
  permissions: CorePermission[];
  memberCount: number;
  saving: boolean;
  error: string | null;
  onClose: () => void;
  onSave: (permissionNames: string[]) => Promise<void>;
}) {
  const t = useT();
  const [selected, setSelected] = useState<string[]>([]);
  const [module, setModule] = useState("_all");

  useEffect(() => {
    setSelected(role?.permissions ?? []);
    setModule("_all");
  }, [role]);

  const modules = useMemo(() => [
    "_all",
    ...Array.from(new Set(permissions.map((permission) => permission.name.split(".")[0]))).sort(),
  ], [permissions]);
  const visible = module === "_all"
    ? permissions
    : permissions.filter((permission) => permission.name.startsWith(`${module}.`));

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onSave(selected);
  }

  return (
    <Dialog
      open={Boolean(role)}
      title={role ? t("Edit role: {role}", { role: role.name }) : t("Edit role")}
      description={t("Modify the permissions shared by every member of this role.")}
      maxWidthClassName="max-w-3xl"
      zIndexClassName="z-[1100]"
      onClose={onClose}
    >
      <form className="space-y-4" onSubmit={submit}>
        {memberCount > 0 ? (
          <Alert title={t("Shared role")}>
            {memberCount === 1
              ? t("Changes will affect 1 user assigned to this role.")
              : t("Changes will affect {count} users assigned to this role.", { count: memberCount })}
          </Alert>
        ) : null}
        <Select
          value={module}
          onChange={setModule}
          options={modules.map((name) => ({
            value: name,
            label: name === "_all" ? t("All modules") : name,
          }))}
        />
        <div className="grid max-h-80 gap-2 overflow-y-auto border border-border bg-card p-3 sm:grid-cols-2">
          {visible.map((permission) => (
            <label key={permission.id} className="flex items-center gap-2 text-sm text-foreground">
              <input
                type="checkbox"
                checked={selected.includes(permission.name)}
                onChange={(event) => setSelected((current) => event.target.checked
                  ? [...current, permission.name]
                  : current.filter((name) => name !== permission.name))}
              />
              <span>{permission.name}</span>
            </label>
          ))}
        </div>
        {error ? <Alert title={t("Could not save the role")}>{error}</Alert> : null}
        <div className="flex justify-end gap-3">
          <Button type="button" variant="secondary" onClick={onClose}>{t("Cancel")}</Button>
          <Button type="submit" disabled={saving}>{t("Save role")}</Button>
        </div>
      </form>
    </Dialog>
  );
}
