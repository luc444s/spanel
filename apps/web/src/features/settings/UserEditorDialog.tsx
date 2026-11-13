import { FormEvent, useEffect, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input, newCredentialProps, noAutofillProps } from "@systutor/shell/ui/input";
import { Select } from "@systutor/shell/ui/select";
import { Tabs } from "@systutor/shell/ui/tabs";
import { useT } from "@/i18n/use-i18n";

import type { CoreBranch, CorePermission, CoreRole, CoreUser } from "../core-management/api";
import { RolePermissionsDialog } from "./RolePermissionsDialog";
import { UserPermissionsPanel } from "./UserPermissionsPanel";

export type UserFormState = {
  id?: string;
  name: string;
  email: string;
  password: string;
  branch_id: string;
};

export function UserEditorDialog({
  open,
  formState,
  users,
  roles,
  branches,
  permissions,
  selectedPermissionUserId,
  selectedRoleId,
  roleEditor,
  canManageRoles,
  canUpdate,
  formError,
  assignmentError,
  roleError,
  savingProfile,
  savingAssignment,
  savingRole,
  onClose,
  onProfileSubmit,
  onFieldChange,
  onSelectPermissionUser,
  onSelectRole,
  onSaveAssignment,
  onOpenRole,
  onCloseRole,
  onSaveRole,
}: {
  open: boolean;
  formState: UserFormState;
  users: CoreUser[];
  roles: CoreRole[];
  branches: CoreBranch[];
  permissions: CorePermission[];
  selectedPermissionUserId: string;
  selectedRoleId: string | null;
  roleEditor: CoreRole | null;
  canManageRoles: boolean;
  canUpdate: boolean;
  formError: string | null;
  assignmentError: string | null;
  roleError: string | null;
  savingProfile: boolean;
  savingAssignment: boolean;
  savingRole: boolean;
  onClose: () => void;
  onProfileSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onFieldChange: (value: Partial<UserFormState>) => void;
  onSelectPermissionUser: (userId: string) => void;
  onSelectRole: (roleId: string) => void;
  onSaveAssignment: () => Promise<void>;
  onOpenRole: (role: CoreRole) => void;
  onCloseRole: () => void;
  onSaveRole: (permissionNames: string[]) => Promise<void>;
}) {
  const t = useT();
  const [tab, setTab] = useState("profile");

  useEffect(() => {
    if (open) setTab(canUpdate ? "profile" : "permissions");
  }, [canUpdate, open, formState.id]);

  const profile = (
    <form className="space-y-4" onSubmit={onProfileSubmit}>
      <label className="block space-y-2 text-sm text-foreground">
        <span>{t("Name")}</span>
        <Input value={formState.name} onChange={(event) => onFieldChange({ name: event.target.value })} />
      </label>
      <label className="block space-y-2 text-sm text-foreground">
        <span>{t("Email")}</span>
        <Input type="email" value={formState.email} onChange={(event) => onFieldChange({ email: event.target.value })} {...noAutofillProps} />
      </label>
      <label className="block space-y-2 text-sm text-foreground">
        <span>{t("Password")}</span>
        <Input type="password" value={formState.password} onChange={(event) => onFieldChange({ password: event.target.value })} {...newCredentialProps} />
      </label>
      <label className="block space-y-2 text-sm text-foreground">
        <span>{t("Branch")}</span>
        <Select
          value={formState.branch_id || ""}
          onChange={(value) => onFieldChange({ branch_id: value })}
          options={[
            { value: "", label: t("No branch") },
            ...branches.map((branch) => ({ value: branch.id, label: t("{code} - {name}", { code: branch.code, name: branch.name }) })),
          ]}
        />
      </label>
      {formError ? <Alert title={t("Could not save the user")}>{formError}</Alert> : null}
      <div className="flex justify-end gap-3">
        <Button type="button" variant="secondary" onClick={onClose}>{t("Cancel")}</Button>
        <Button type="submit" disabled={savingProfile}>
          {formState.id ? t("Save changes") : t("Create user")}
        </Button>
      </div>
    </form>
  );

  const permissionPanel = (
    <UserPermissionsPanel
      users={users}
      roles={roles}
      selectedUserId={selectedPermissionUserId}
      selectedRoleId={selectedRoleId}
      saving={savingAssignment}
      error={assignmentError}
      onSelectUser={onSelectPermissionUser}
      onSelectRole={onSelectRole}
      onOpenRole={onOpenRole}
      onCancel={onClose}
      onSave={onSaveAssignment}
    />
  );

  return <>
    <Dialog
      open={open}
      title={formState.id ? t("Edit user") : t("Create user")}
      description={t("Tenant user profile and access management.")}
      maxWidthClassName="max-w-2xl"
      onClose={onClose}
    >
      {canManageRoles ? (
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            {
              value: "profile",
              label: t("Profile"),
              content: profile,
              disabled: Boolean(formState.id) && !canUpdate,
            },
            {
              value: "permissions",
              label: t("Roles"),
              content: permissionPanel,
              disabled: !formState.id,
            },
          ]}
        />
      ) : profile}
    </Dialog>
    <RolePermissionsDialog
      role={roleEditor}
      permissions={permissions}
      saving={savingRole}
      error={roleError}
      onClose={onCloseRole}
      onSave={onSaveRole}
    />
  </>;
}
