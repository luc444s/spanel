import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Alert } from "@systutor/shell/ui/alert";
import { Badge } from "@systutor/shell/ui/badge";
import { Button } from "@systutor/shell/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@systutor/shell/ui/card";
import { DataTable } from "@systutor/shell/ui/data-table";
import { useT } from "@/i18n/use-i18n";

import {
  CoreRole,
  CoreUser,
  coreManagementKeys,
  createCoreUser,
  disableCoreUser,
  enableCoreUser,
  invalidateCoreManagementKey,
  listCoreBranches,
  listCorePermissions,
  listCoreRoles,
  listCoreUsers,
  replaceCoreUserRole,
  updateCoreRole,
  updateCoreUser,
} from "../core-management/api";
import { useAuthStore } from "../auth/store";
import { UserEditorDialog, type UserFormState } from "./UserEditorDialog";
import { assignedRoleId, roleMemberCount } from "./user-permissions";

const EMPTY_USER_FORM: UserFormState = {
  name: "", email: "", password: "", branch_id: "",
};

export function UsersPage() {
  const t = useT();
  const queryClient = useQueryClient();
  const sessionPermissions = useAuthStore((state) => state.permissions);
  const canCreate = sessionPermissions.includes("core.users.create");
  const canUpdate = sessionPermissions.includes("core.users.update");
  const canDisable = sessionPermissions.includes("core.users.disable");
  const canManageRoles = sessionPermissions.includes("core.roles.manage");
  const canOpenEditor = canUpdate || canManageRoles;

  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [formState, setFormState] = useState<UserFormState>(EMPTY_USER_FORM);
  const [selectedPermissionUserId, setSelectedPermissionUserId] = useState("");
  const [selectedRoleId, setSelectedRoleId] = useState<string | null>(null);
  const [roleEditor, setRoleEditor] = useState<CoreRole | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [assignmentError, setAssignmentError] = useState<string | null>(null);
  const [roleError, setRoleError] = useState<string | null>(null);

  const usersQuery = useQuery({ queryKey: [...coreManagementKeys.users], queryFn: listCoreUsers });
  const rolesQuery = useQuery({
    queryKey: [...coreManagementKeys.roles], queryFn: listCoreRoles,
    enabled: canOpenEditor || canCreate,
  });
  const branchesQuery = useQuery({
    queryKey: [...coreManagementKeys.branches], queryFn: listCoreBranches,
    enabled: canOpenEditor || canCreate,
  });
  const permissionsQuery = useQuery({
    queryKey: [...coreManagementKeys.permissions], queryFn: listCorePermissions,
    enabled: isDialogOpen && canManageRoles,
  });
  const users = usersQuery.data ?? [];
  const roles = useMemo(() => (rolesQuery.data ?? []).filter((role) => role.active), [rolesQuery.data]);

  const saveUserMutation = useMutation({
    mutationFn: async (payload: UserFormState) => payload.id
      ? updateCoreUser(payload.id, {
          name: payload.name,
          email: payload.email,
          password: payload.password || undefined,
          branch_id: payload.branch_id || null,
        })
      : createCoreUser({
          name: payload.name,
          email: payload.email,
          password: payload.password,
          branch_id: payload.branch_id || null,
          role_ids: [],
        }),
    onSuccess: async () => {
      await Promise.all([
        invalidateCoreManagementKey(queryClient, coreManagementKeys.users),
        invalidateCoreManagementKey(queryClient, coreManagementKeys.roles),
      ]);
      closeEditor();
    },
  });
  const toggleUserMutation = useMutation({
    mutationFn: ({ userId, active }: { userId: string; active: boolean }) => active
      ? disableCoreUser(userId) : enableCoreUser(userId),
    onSuccess: () => invalidateCoreManagementKey(queryClient, coreManagementKeys.users),
  });
  const assignmentMutation = useMutation({
    mutationFn: ({ userId, roleId }: { userId: string; roleId: string }) =>
      replaceCoreUserRole(userId, roleId),
    onSuccess: async () => {
      await Promise.all([
        invalidateCoreManagementKey(queryClient, coreManagementKeys.users),
        queryClient.invalidateQueries({ queryKey: ["auth", "me"] }),
        queryClient.invalidateQueries({ queryKey: ["system", "plugin-runtime"] }),
      ]);
    },
  });
  const roleMutation = useMutation({
    mutationFn: ({ roleId, permissionNames }: { roleId: string; permissionNames: string[] }) =>
      updateCoreRole(roleId, { permission_names: permissionNames }),
    onSuccess: async () => {
      await Promise.all([
        invalidateCoreManagementKey(queryClient, coreManagementKeys.roles),
        queryClient.invalidateQueries({ queryKey: ["auth", "me"] }),
        queryClient.invalidateQueries({ queryKey: ["system", "plugin-runtime"] }),
      ]);
      setRoleEditor(null);
    },
  });

  function closeEditor() {
    setIsDialogOpen(false);
    setFormState(EMPTY_USER_FORM);
    setRoleEditor(null);
    setFormError(null);
    setAssignmentError(null);
    setRoleError(null);
  }

  function selectPermissionUser(userId: string) {
    const user = users.find((item) => item.id === userId);
    setSelectedPermissionUserId(userId);
    setSelectedRoleId(assignedRoleId(user, roles));
    if (user) {
      setFormState({
        id: user.id,
        name: user.name,
        email: user.email,
        password: "",
        branch_id: user.branch_id ?? "",
      });
    }
    setAssignmentError(null);
  }

  function openCreateDialog() {
    setFormState(EMPTY_USER_FORM);
    setSelectedPermissionUserId("");
    setSelectedRoleId(null);
    setFormError(null);
    setIsDialogOpen(true);
  }

  function openEditDialog(user: CoreUser) {
    setFormState({
      id: user.id, name: user.name, email: user.email, password: "", branch_id: user.branch_id ?? "",
    });
    setSelectedPermissionUserId(user.id);
    setSelectedRoleId(assignedRoleId(user, roles));
    setFormError(null);
    setAssignmentError(null);
    setIsDialogOpen(true);
  }

  async function submitProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    if (!formState.id && !formState.password) {
      setFormError(t("Password is required when creating a user."));
      return;
    }
    try {
      await saveUserMutation.mutateAsync(formState);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : t("Could not save the user."));
    }
  }

  async function saveAssignment() {
    if (!selectedPermissionUserId || !selectedRoleId) return;
    setAssignmentError(null);
    try {
      await assignmentMutation.mutateAsync({
        userId: selectedPermissionUserId, roleId: selectedRoleId,
      });
    } catch (error) {
      setAssignmentError(error instanceof Error ? error.message : t("Could not save the assignment."));
    }
  }

  async function saveRole(permissionNames: string[]) {
    if (!roleEditor) return;
    setRoleError(null);
    try {
      await roleMutation.mutateAsync({ roleId: roleEditor.id, permissionNames });
    } catch (error) {
      setRoleError(error instanceof Error ? error.message : t("Could not save the role."));
    }
  }

  const hasError = Boolean(
    usersQuery.error || rolesQuery.error || branchesQuery.error || permissionsQuery.error,
  );

  return <section className="space-y-6">
    <div className="flex items-start justify-between gap-4">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold text-foreground">{t("Users")}</h1>
        <p className="text-sm text-muted-foreground">{t("Tenant-aware administration of users, branches, and effective core roles.")}</p>
      </div>
      {canCreate ? <Button onClick={openCreateDialog}>{t("Create user")}</Button> : null}
    </div>
    {hasError ? <Alert title={t("Could not load users")}>{t("Check permissions or backend connectivity.")}</Alert> : null}
    <Card>
      <CardHeader>
        <CardTitle>{t("Users")}</CardTitle>
        <CardDescription>{t("Minimal operational list for the current tenant.")}</CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={[
            { key: "name", header: t("Name"), render: (user) => user.name },
            { key: "email", header: t("Email"), render: (user) => user.email },
            { key: "branch", header: t("Branch"), render: (user) => (branchesQuery.data ?? []).find((branch) => branch.id === user.branch_id)?.name ?? "-" },
            { key: "roles", header: t("Role"), render: (user) => user.roles[0] ? <Badge>{user.roles[0]}</Badge> : "-" },
            { key: "status", header: t("Status"), render: (user) => <Badge>{user.active ? t("Active") : t("Inactive")}</Badge> },
            { key: "actions", header: t("Actions"), className: "w-56", render: (user) => <div className="flex gap-2">
              {canOpenEditor ? <Button variant="secondary" onClick={() => openEditDialog(user)}>{t("Edit")}</Button> : null}
              {canDisable ? <Button variant="secondary" disabled={toggleUserMutation.isPending} onClick={() => toggleUserMutation.mutate({ userId: user.id, active: user.active })}>{user.active ? t("Deactivate") : t("Activate")}</Button> : null}
            </div> },
          ]}
          rows={users}
          rowKey={(user) => user.id}
          emptyMessage={t("No users in this tenant.")}
        />
      </CardContent>
    </Card>
    <UserEditorDialog
      open={isDialogOpen}
      formState={formState}
      users={users}
      roles={roles}
      branches={branchesQuery.data ?? []}
      permissions={permissionsQuery.data ?? []}
      selectedPermissionUserId={selectedPermissionUserId}
      selectedRoleId={selectedRoleId}
      roleEditor={roleEditor}
      roleMemberCount={roleEditor ? roleMemberCount(roleEditor, users) : 0}
      canManageRoles={canManageRoles}
      canUpdate={canUpdate}
      formError={formError}
      assignmentError={assignmentError}
      roleError={roleError}
      savingProfile={saveUserMutation.isPending}
      savingAssignment={assignmentMutation.isPending}
      savingRole={roleMutation.isPending}
      onClose={closeEditor}
      onProfileSubmit={submitProfile}
      onFieldChange={(value) => setFormState((current) => ({ ...current, ...value }))}
      onSelectPermissionUser={selectPermissionUser}
      onSelectRole={setSelectedRoleId}
      onSaveAssignment={saveAssignment}
      onOpenRole={setRoleEditor}
      onCloseRole={() => setRoleEditor(null)}
      onSaveRole={saveRole}
    />
  </section>;
}
