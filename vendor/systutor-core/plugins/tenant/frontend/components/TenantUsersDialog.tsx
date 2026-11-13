import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { RolePermissionsDialog } from "@/features/settings/RolePermissionsDialog";
import { useT } from "@/i18n/use-i18n";
import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input, newCredentialProps, noAutofillProps } from "@systutor/shell/ui/input";
import { Select } from "@systutor/shell/ui/select";
import { Tabs } from "@systutor/shell/ui/tabs";

import {
  createUserInTenant,
  listTenantPermissions,
  listTenantRoles,
  listTenantUsers,
  reassignUserFromTenant,
  replaceTenantUserRole,
  tenantKeys,
  updateTenantRolePermissions,
  type TenantResponse,
  type TenantRoleResponse,
} from "../api";

export function TenantUsersDialog({ tenant, onClose }: {
  tenant: TenantResponse | null;
  onClose: () => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const tenantId = tenant?.id ?? "";
  const [tab, setTab] = useState("profile");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [profileError, setProfileError] = useState<string | null>(null);
  const [selectedUserId, setSelectedUserId] = useState("");
  const [selectedRoleId, setSelectedRoleId] = useState<string | null>(null);
  const [assignmentError, setAssignmentError] = useState<string | null>(null);
  const [roleEditor, setRoleEditor] = useState<TenantRoleResponse | null>(null);
  const [roleError, setRoleError] = useState<string | null>(null);
  const [reassignUserId, setReassignUserId] = useState<string | null>(null);
  const [targetTenantId, setTargetTenantId] = useState("");
  const [reassignError, setReassignError] = useState<string | null>(null);

  const usersQuery = useQuery({
    queryKey: tenantKeys.users(tenantId),
    queryFn: () => listTenantUsers(tenantId),
    enabled: Boolean(tenant),
  });
  const rolesQuery = useQuery({
    queryKey: tenantKeys.roles(tenantId),
    queryFn: () => listTenantRoles(tenantId),
    enabled: Boolean(tenant),
  });
  const permissionsQuery = useQuery({
    queryKey: tenantKeys.permissions(tenantId),
    queryFn: () => listTenantPermissions(tenantId),
    enabled: Boolean(tenant),
  });
  const users = usersQuery.data?.users ?? [];
  const roles = useMemo(
    () => (rolesQuery.data?.roles ?? []).filter((role) => role.active),
    [rolesQuery.data],
  );

  useEffect(() => {
    if (!tenant) return;
    setTab("profile");
    setSelectedUserId("");
    setSelectedRoleId(null);
    setRoleEditor(null);
  }, [tenant]);

  useEffect(() => {
    if (!selectedUserId && users[0]) setSelectedUserId(users[0].id);
  }, [selectedUserId, users]);

  useEffect(() => {
    const user = users.find((item) => item.id === selectedUserId);
    const role = roles.find((item) => user?.roles.includes(item.name));
    setSelectedRoleId(role?.id ?? null);
  }, [roles, selectedUserId, users]);

  const createMutation = useMutation({
    mutationFn: () => createUserInTenant(tenantId, { email, password, full_name: fullName }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: tenantKeys.users(tenantId) });
      setEmail(""); setPassword(""); setFullName(""); setProfileError(null);
      toast.success(t("User created"));
    },
    onError: (error: Error) => setProfileError(error.message),
  });
  const assignmentMutation = useMutation({
    mutationFn: () => replaceTenantUserRole(tenantId, selectedUserId, selectedRoleId!),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: tenantKeys.users(tenantId) });
      setAssignmentError(null);
      toast.success(t("Role assigned"));
    },
    onError: (error: Error) => setAssignmentError(error.message),
  });
  const roleMutation = useMutation({
    mutationFn: (permissionNames: string[]) =>
      updateTenantRolePermissions(tenantId, roleEditor!.id, permissionNames),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: tenantKeys.roles(tenantId) });
      setRoleEditor(null); setRoleError(null);
      toast.success(t("Role updated"));
    },
    onError: (error: Error) => setRoleError(error.message),
  });
  const reassignMutation = useMutation({
    mutationFn: () => reassignUserFromTenant(tenantId, reassignUserId!, { target_tenant_id: targetTenantId }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: tenantKeys.users(tenantId) });
      setReassignUserId(null); setTargetTenantId(""); setReassignError(null);
      toast.success(t("User reassigned"));
    },
    onError: (error: Error) => setReassignError(error.message),
  });

  function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setProfileError(null);
    if (!email.trim()) return setProfileError(t("Email required"));
    if (!password.trim()) return setProfileError(t("Password required"));
    if (password.length < 8) return setProfileError(t("Password must be at least 8 characters"));
    createMutation.mutate();
  }

  const profile = <div className="space-y-3">
    <form onSubmit={submitCreate} className="space-y-2 rounded-md border border-border p-3">
      <p className="text-xs font-semibold uppercase text-muted-foreground">{t("Create user")}</p>
      <label className="block space-y-1 text-sm text-foreground"><span>{t("Email")}</span>
        <Input type="email" value={email} onChange={(event) => setEmail(event.target.value)} {...noAutofillProps} />
      </label>
      <label className="block space-y-1 text-sm text-foreground"><span>{t("Password")}</span>
        <Input type="password" value={password} onChange={(event) => setPassword(event.target.value)} {...newCredentialProps} />
      </label>
      <label className="block space-y-1 text-sm text-foreground"><span>{t("Full name (optional)")}</span>
        <Input value={fullName} onChange={(event) => setFullName(event.target.value)} />
      </label>
      {profileError ? <Alert title={t("Error")}>{profileError}</Alert> : null}
      <div className="flex justify-end"><Button type="submit" size="sm" disabled={createMutation.isPending}>{t("Create user")}</Button></div>
    </form>
    {usersQuery.isLoading ? <p className="text-sm text-muted-foreground">{t("Loading users...")}</p> : null}
    <div className="space-y-1">{users.map((user) => <div key={user.id} className="flex items-center justify-between border border-border px-3 py-2">
      <div><span className="text-sm text-foreground">{user.email}</span><span className="ml-2 text-xs text-muted-foreground">({user.full_name})</span></div>
      <Button size="sm" variant="secondary" onClick={() => { setReassignUserId(user.id); setTargetTenantId(""); }}>{t("Remove")}</Button>
    </div>)}</div>
  </div>;

  const rolePanel = <div className="space-y-4">
    <label className="block space-y-2 text-sm text-foreground"><span>{t("Select user")}</span>
      <Select value={selectedUserId} onChange={setSelectedUserId} options={users.map((user) => ({ value: user.id, label: user.full_name ? `${user.full_name} — ${user.email}` : user.email }))} />
    </label>
    <div className="space-y-2" role="radiogroup" aria-label={t("Available roles")}>{roles.map((role) => {
      const checked = role.id === selectedRoleId;
      return <button key={role.id} type="button" role="radio" aria-checked={checked}
        className="flex w-full items-center gap-3 border border-border bg-card px-3 py-3 text-left text-sm text-foreground hover:border-primary"
        onClick={() => setSelectedRoleId(role.id)} onDoubleClick={() => setRoleEditor(role)}>
        <span aria-hidden="true" className="font-mono">{checked ? "[✓]" : "[ ]"}</span><span className="font-medium">{role.name}</span>
      </button>;
    })}</div>
    <p className="text-xs text-muted-foreground">{t("Select one role for the user. Double-click a role to modify its permissions.")}</p>
    {assignmentError ? <Alert title={t("Error")}>{assignmentError}</Alert> : null}
    <div className="flex justify-end"><Button disabled={!selectedUserId || !selectedRoleId || assignmentMutation.isPending} onClick={() => assignmentMutation.mutate()}>{t("Save assignment")}</Button></div>
  </div>;

  return <>
    <Dialog open={Boolean(tenant)} title={t("Users — {name}", { name: tenant?.name ?? "" })} onClose={onClose} maxWidthClassName="max-w-2xl">
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "profile", label: t("Profile"), content: profile }, { value: "roles", label: t("Roles"), content: rolePanel }]} />
    </Dialog>
    <RolePermissionsDialog role={roleEditor} permissions={permissionsQuery.data?.permissions ?? []} saving={roleMutation.isPending} error={roleError} onClose={() => setRoleEditor(null)} onSave={(names) => roleMutation.mutateAsync(names)} />
    <Dialog open={Boolean(reassignUserId)} title={t("Reassign user")} onClose={() => setReassignUserId(null)}>
      <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); if (!targetTenantId.trim()) return setReassignError(t("Target tenant ID required")); reassignMutation.mutate(); }}>
        <label className="block space-y-1 text-sm text-foreground"><span>{t("Target Tenant ID")}</span><Input value={targetTenantId} onChange={(event) => setTargetTenantId(event.target.value)} /></label>
        {reassignError ? <Alert title={t("Error")}>{reassignError}</Alert> : null}
        <div className="flex justify-end"><Button type="submit" disabled={reassignMutation.isPending}>{t("Reassign")}</Button></div>
      </form>
    </Dialog>
  </>;
}
