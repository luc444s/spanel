import { FormEvent } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Badge } from "@systutor/shell/ui/badge";
import { Button } from "@systutor/shell/ui/button";
import { Select } from "@systutor/shell/ui/select";
import { useT } from "@/i18n/use-i18n";

import type { CoreRole, CoreUser } from "../core-management/api";

export function UserPermissionsPanel({
  users,
  roles,
  selectedUserId,
  selectedRoleId,
  saving,
  error,
  onSelectUser,
  onSelectRole,
  onOpenRole,
  onCancel,
  onSave,
}: {
  users: CoreUser[];
  roles: CoreRole[];
  selectedUserId: string;
  selectedRoleId: string | null;
  saving: boolean;
  error: string | null;
  onSelectUser: (userId: string) => void;
  onSelectRole: (roleId: string) => void;
  onOpenRole: (role: CoreRole) => void;
  onCancel: () => void;
  onSave: () => Promise<void>;
}) {
  const t = useT();
  const selectedUser = users.find((user) => user.id === selectedUserId);
  const legacyAssignments = (selectedUser?.roles.length ?? 0) > 1;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onSave();
  }

  return (
    <form className="space-y-4" onSubmit={submit}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-foreground">{t("Permissions")}</h3>
          <p className="text-sm text-muted-foreground">{t("Access management")}</p>
        </div>
        <Badge>RBAC</Badge>
      </div>

      <label className="block space-y-2 text-sm text-foreground">
        <span>{t("Select user")}</span>
        <Select
          value={selectedUserId}
          onChange={onSelectUser}
          options={users.map((user) => ({
            value: user.id,
            label: user.category ? `${user.name} — ${user.category}` : `${user.name} — ${user.email}`,
          }))}
        />
      </label>

      {legacyAssignments ? (
        <Alert title={t("Multiple roles detected")}>
          {t("Choose the single role this user should keep. Saving will replace the legacy assignments.")}
        </Alert>
      ) : null}

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-foreground">{t("Available roles")}</legend>
        <p className="text-xs text-muted-foreground">
          {selectedRoleId ? t("1 assigned") : t("No role selected")}
        </p>
        <div className="grid gap-2" role="radiogroup" aria-label={t("Available roles")}>
          {roles.map((role) => {
            const checked = role.id === selectedRoleId;
            return (
              <button
                key={role.id}
                type="button"
                role="radio"
                aria-checked={checked}
                className="flex w-full items-center gap-3 border border-border bg-card px-3 py-3 text-left text-sm text-foreground transition hover:border-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => onSelectRole(role.id)}
                onDoubleClick={() => onOpenRole(role)}
              >
                <span aria-hidden="true" className="font-mono">{checked ? "[✓]" : "[ ]"}</span>
                <span className="font-medium">{role.name}</span>
              </button>
            );
          })}
        </div>
      </fieldset>

      <p className="text-xs text-muted-foreground">
        {t("Select one role for the user. Double-click a role to modify its permissions.")}
      </p>
      {error ? <Alert title={t("Could not save the assignment")}>{error}</Alert> : null}
      <div className="flex justify-end gap-3">
        <Button type="button" variant="secondary" onClick={onCancel}>{t("Cancel")}</Button>
        <Button type="submit" disabled={saving || !selectedRoleId}>{t("Save assignment")}</Button>
      </div>
    </form>
  );
}
