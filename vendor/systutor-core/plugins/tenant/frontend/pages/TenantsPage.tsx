import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { toast } from "sonner";

import {
  addTenantDomain,
  assignUserToTenant,
  createTenant,
  createUserInTenant,
  listTenants,
  listTenantDomains,
  listTenantUsers,
  reassignUserFromTenant,
  removeTenantDomain,
  tenantKeys,
  updateTenant,
  type TenantResponse,
  type TenantUserResponse,
} from "../api";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input, newCredentialProps, noAutofillProps } from "@systutor/shell/ui/input";
import { Alert } from "@systutor/shell/ui/alert";
import { useT } from "@/i18n/use-i18n";

export default function TenantsPage() {
  const t = useT();
  const queryClient = useQueryClient();

  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createSlug, setCreateSlug] = useState("");
  const [createDomain, setCreateDomain] = useState("");
  const [createUserEmail, setCreateUserEmail] = useState("");
  const [createUserPassword, setCreateUserPassword] = useState("");
  const [createUserFullName, setCreateUserFullName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);

  const [selectedTenant, setSelectedTenant] = useState<TenantResponse | null>(null);
  const [editDomain, setEditDomain] = useState("");
  const [editSiteSuffix, setEditSiteSuffix] = useState("");
  const [editError, setEditError] = useState<string | null>(null);
  const [newManagedDomain, setNewManagedDomain] = useState("");
  const [domainError, setDomainError] = useState<string | null>(null);

  const [showUsers, setShowUsers] = useState<TenantResponse | null>(null);
  const [newUserEmail, setNewUserEmail] = useState("");
  const [newUserPassword, setNewUserPassword] = useState("");
  const [newUserFullName, setNewUserFullName] = useState("");
  const [newUserError, setNewUserError] = useState<string | null>(null);
  const [reassignUserId, setReassignUserId] = useState<string | null>(null);
  const [reassignTargetTenantId, setReassignTargetTenantId] = useState("");
  const [reassignError, setReassignError] = useState<string | null>(null);

  const tenantsQuery = useQuery({ queryKey: tenantKeys.tenants, queryFn: listTenants });

  const usersQuery = useQuery({
    queryKey: tenantKeys.users(showUsers?.id ?? ""),
    queryFn: () => listTenantUsers(showUsers!.id),
    enabled: Boolean(showUsers),
  });

  const domainsQuery = useQuery({
    queryKey: tenantKeys.domains(selectedTenant?.id ?? ""),
    queryFn: () => listTenantDomains(selectedTenant!.id),
    enabled: Boolean(selectedTenant),
  });

  const createMutation = useMutation({
    mutationFn: createTenant,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: tenantKeys.tenants });
      setShowCreate(false);
      setCreateName("");
      setCreateSlug("");
      setCreateDomain("");
      setCreateUserEmail("");
      setCreateUserPassword("");
      setCreateUserFullName("");
      setCreateError(null);
      toast.success(t("Tenant created"));
    },
    onError: (e: Error) => setCreateError(e.message),
  });

  const updatePrimaryMutation = useMutation({
    mutationFn: ({ tenantId, domain, site_suffix }: { tenantId: string; domain: string | null; site_suffix: string | null }) =>
      updateTenant(tenantId, { domain, site_suffix }),
    onSuccess: async (_, vars) => {
      await queryClient.invalidateQueries({ queryKey: tenantKeys.tenants });
      await queryClient.invalidateQueries({ queryKey: tenantKeys.domains(vars.tenantId) });
      setEditError(null);
      toast.success(t("Primary domain updated"));
    },
    onError: (e: Error) => setEditError(e.message),
  });

  const addDomainMutation = useMutation({
    mutationFn: ({ tenantId, domain }: { tenantId: string; domain: string }) =>
      addTenantDomain(tenantId, domain),
    onSuccess: async (_, vars) => {
      await queryClient.invalidateQueries({ queryKey: tenantKeys.domains(vars.tenantId) });
      await queryClient.invalidateQueries({ queryKey: tenantKeys.tenants });
      setNewManagedDomain("");
      setDomainError(null);
      toast.success(t("Domain added"));
    },
    onError: (e: Error) => setDomainError(e.message),
  });

  const removeDomainMutation = useMutation({
    mutationFn: ({ tenantId, domain }: { tenantId: string; domain: string }) =>
      removeTenantDomain(tenantId, domain),
    onSuccess: async (_, vars) => {
      await queryClient.invalidateQueries({ queryKey: tenantKeys.domains(vars.tenantId) });
      await queryClient.invalidateQueries({ queryKey: tenantKeys.tenants });
      setDomainError(null);
      toast.success(t("Domain removed"));
    },
    onError: (e: Error) => setDomainError(e.message),
  });

  const createUserMutation = useMutation({
    mutationFn: ({ tenantId, email, password, full_name }: { tenantId: string; email: string; password: string; full_name: string }) =>
      createUserInTenant(tenantId, { email, password, full_name }),
    onSuccess: async (_, vars) => {
      await queryClient.invalidateQueries({ queryKey: tenantKeys.users(vars.tenantId) });
      setNewUserEmail("");
      setNewUserPassword("");
      setNewUserFullName("");
      setNewUserError(null);
      toast.success(t("User created"));
    },
    onError: (e: Error) => setNewUserError(e.message),
  });

  const removeMutation = useMutation({
    mutationFn: ({ tenantId, userId, targetTenantId }: { tenantId: string; userId: string; targetTenantId: string }) =>
      reassignUserFromTenant(tenantId, userId, { target_tenant_id: targetTenantId }),
    onSuccess: async (_, vars) => {
      await queryClient.invalidateQueries({ queryKey: tenantKeys.users(vars.tenantId) });
      setReassignUserId(null);
      setReassignTargetTenantId("");
      setReassignError(null);
      toast.success(t("User reassigned"));
    },
    onError: (e: Error) => setReassignError(e.message),
  });

  function handleCreate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setCreateError(null);
    if (!createName.trim()) { setCreateError(t("Name required")); return; }
    if (!createSlug.trim()) { setCreateError(t("Slug required")); return; }
    if (!createUserEmail.trim()) { setCreateError(t("User email required")); return; }
    if (!createUserPassword.trim()) { setCreateError(t("User password required")); return; }
    if (createUserPassword.length < 8) { setCreateError(t("Password must be at least 8 characters")); return; }
    createMutation.mutate({
      name: createName,
      slug: createSlug,
      domain: createDomain || undefined,
      user_email: createUserEmail,
      user_password: createUserPassword,
      user_full_name: createUserFullName || undefined,
    });
  }

  function handleUpdatePrimary(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setEditError(null);
    if (!selectedTenant) return;
    updatePrimaryMutation.mutate({
      tenantId: selectedTenant.id,
      domain: editDomain.trim() || null,
      site_suffix: editSiteSuffix.trim() || null,
    });
  }

  function handleAddManagedDomain() {
    setDomainError(null);
    if (!selectedTenant) return;
    if (!newManagedDomain.trim()) { setDomainError(t("Domain required")); return; }
    addDomainMutation.mutate({ tenantId: selectedTenant.id, domain: newManagedDomain.trim() });
  }

  function handleRemoveManagedDomain(domain: string) {
    setDomainError(null);
    if (!selectedTenant) return;
    removeDomainMutation.mutate({ tenantId: selectedTenant.id, domain });
  }

  function handleCreateUser(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setNewUserError(null);
    if (!showUsers) return;
    if (!newUserEmail.trim()) { setNewUserError(t("Email required")); return; }
    if (!newUserPassword.trim()) { setNewUserError(t("Password required")); return; }
    if (newUserPassword.length < 8) { setNewUserError(t("Password must be at least 8 characters")); return; }
    createUserMutation.mutate({
      tenantId: showUsers.id,
      email: newUserEmail,
      password: newUserPassword,
      full_name: newUserFullName,
    });
  }

  if (tenantsQuery.isLoading) {
    return <div className="flex min-h-[200px] items-center justify-center text-sm text-muted-foreground">{t("Loading...")}</div>;
  }

  if (tenantsQuery.error) {
    return <Alert title={t("Error")}>{t("Could not load tenants.")}</Alert>;
  }

  const tenants = tenantsQuery.data?.tenants ?? [];

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-foreground">{t("Tenants")}</h1>
        <Button size="sm" onClick={() => { setShowCreate(true); setCreateError(null); }}>
        {t("Create tenant")}
      </Button>
    </div>

      <Dialog open={showCreate} title={t("Create tenant")} onClose={() => setShowCreate(false)}>
        <form className="space-y-3" onSubmit={handleCreate}>
          <label className="block space-y-1 text-sm text-foreground">
            <span>{t("Name")}</span>
            <Input type="text" value={createName} onChange={(e) => setCreateName(e.target.value)} placeholder={t("My Company")} autoFocus />
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>{t("Slug")}</span>
            <Input type="text" value={createSlug} onChange={(e) => setCreateSlug(e.target.value)} placeholder={t("my-company")} />
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>{t("Domain")}</span>
            <Input type="text" value={createDomain} onChange={(e) => setCreateDomain(e.target.value)} placeholder={t("company.com")} />
          </label>
          <div className="border-t border-border pt-3 mt-1">
            <p className="text-xs font-semibold uppercase text-muted-foreground mb-2">{t("Tenant base user")}</p>
          </div>
          <label className="block space-y-1 text-sm text-foreground">
            <span>{t("Email")}</span>
            <Input type="email" value={createUserEmail} onChange={(e) => setCreateUserEmail(e.target.value)} placeholder={t("admin@company.com")} {...noAutofillProps} />
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>{t("Password")}</span>
            <Input type="password" value={createUserPassword} onChange={(e) => setCreateUserPassword(e.target.value)} placeholder={t("Minimum 8 characters")} {...newCredentialProps} />
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>{t("Full name (optional)")}</span>
            <Input type="text" value={createUserFullName} onChange={(e) => setCreateUserFullName(e.target.value)} placeholder={t("John Doe")} />
          </label>
          {createError && <Alert title={t("Error")}>{createError}</Alert>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={() => setShowCreate(false)}>{t("Cancel")}</Button>
            <Button type="submit" size="sm" disabled={createMutation.isPending}>{createMutation.isPending ? "..." : t("Create")}</Button>
          </div>
        </form>
      </Dialog>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-muted-foreground">
              <th className="pb-2 pr-4">{t("Name")}</th>
              <th className="pb-2 pr-4">{t("Slug")}</th>
              <th className="pb-2 pr-4">{t("Domain")}</th>
              <th className="pb-2 pr-4">{t("Site suffix")}</th>
              <th className="pb-2 pr-4">{t("Status")}</th>
              <th className="pb-2">{t("Actions")}</th>
            </tr>
          </thead>
          <tbody>
            {tenants.map((tenant) => (
              <tr key={tenant.id} className="border-b border-border/50">
                <td className="py-2 pr-4 text-foreground">{tenant.name}</td>
                <td className="py-2 pr-4 font-mono text-xs text-muted-foreground">{tenant.slug}</td>
                <td className="py-2 pr-4 text-foreground">{tenant.domain ?? "—"}</td>
                <td className="py-2 pr-4 font-mono text-xs text-muted-foreground">{tenant.site_suffix ?? "—"}</td>
                <td className="py-2 pr-4">
                  <span className={`inline-block rounded px-1.5 py-0.5 text-xs ${tenant.is_active ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>
                    {tenant.is_active ? t("Active") : t("Inactive")}
                  </span>
                </td>
                <td className="py-2">
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => { setSelectedTenant(tenant); setEditDomain(tenant.domain ?? ""); setEditSiteSuffix(tenant.site_suffix ?? ""); setEditError(null); setNewManagedDomain(""); setDomainError(null); }}
                    >
                      {t("Domains")}
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => { setShowUsers(tenant); setAssignUserId(""); setAssignError(null); }}
                    >
                      {t("Users")}
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
            {tenants.length === 0 && (
              <tr><td colSpan={6} className="py-4 text-center text-muted-foreground">{t("No tenants")}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <Dialog open={Boolean(selectedTenant)} title={t("Domain and suffix")} description={selectedTenant?.name} onClose={() => setSelectedTenant(null)}>
        <div className="space-y-4">
          <form className="space-y-3" onSubmit={handleUpdatePrimary}>
            <label className="block space-y-1 text-sm text-foreground">
              <span>{t("Primary domain (DMS)")}</span>
              <Input type="text" value={editDomain} onChange={(e) => { setEditDomain(e.target.value); setEditError(null); }} placeholder={t("company.com")} autoFocus />
              <span className="text-xs text-muted-foreground">{t("Default mail domain for the tenant.")}</span>
            </label>
            <label className="block space-y-1 text-sm text-foreground">
              <span>{t("Site suffix")}</span>
              <Input type="text" value={editSiteSuffix} onChange={(e) => { setEditSiteSuffix(e.target.value); setEditError(null); }} placeholder={t("company.com")} />
            </label>
            {editError && <Alert title={t("Error")}>{editError}</Alert>}
            <div className="flex justify-end gap-2">
              <Button type="submit" size="sm" disabled={updatePrimaryMutation.isPending}>{updatePrimaryMutation.isPending ? "..." : t("Save")}</Button>
            </div>
          </form>

          <div className="space-y-2 border-t border-border pt-3">
            <p className="text-xs font-semibold uppercase text-muted-foreground">{t("Additional domains")}</p>
            {domainsQuery.isLoading && (
              <p className="text-sm text-muted-foreground">{t("Loading domains...")}</p>
            )}
            {domainsQuery.data?.domains?.filter((item) => item.domain !== selectedTenant?.domain).map((item) => (
              <div key={item.domain} className="flex items-center justify-between rounded border border-border px-3 py-1.5">
                <span className="font-mono text-xs text-foreground">{item.domain}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  disabled={removeDomainMutation.isPending}
                  onClick={() => handleRemoveManagedDomain(item.domain)}
                >
                  {t("Remove")}
                </Button>
              </div>
            ))}
            {domainsQuery.data && domainsQuery.data.domains.filter((item) => item.domain !== selectedTenant?.domain).length === 0 && (
              <p className="text-sm text-muted-foreground">{t("No additional domains.")}</p>
            )}
            <div className="flex gap-2">
              <Input
                type="text"
                value={newManagedDomain}
                onChange={(e) => { setNewManagedDomain(e.target.value); setDomainError(null); }}
                placeholder={t("debug.com")}
              />
              <Button
                type="button"
                size="sm"
                disabled={addDomainMutation.isPending}
                onClick={handleAddManagedDomain}
              >
                {t("Add")}
              </Button>
            </div>
            {domainError && <Alert title={t("Error")}>{domainError}</Alert>}
          </div>

          <div className="flex justify-end">
            <Button type="button" variant="secondary" size="sm" onClick={() => setSelectedTenant(null)}>{t("Close")}</Button>
          </div>
        </div>
      </Dialog>

      <Dialog open={Boolean(showUsers)} title={t("Users — {name}", { name: showUsers?.name ?? "" })} onClose={() => setShowUsers(null)}>
        <div className="space-y-3">
          <form onSubmit={handleCreateUser} className="space-y-2 rounded-md border border-border p-3">
            <p className="text-xs font-semibold uppercase text-muted-foreground">{t("Create user")}</p>
            <label className="block space-y-1 text-sm text-foreground">
              <span>{t("Email")}</span>
              <Input type="email" value={newUserEmail} onChange={(e) => setNewUserEmail(e.target.value)} placeholder={t("user@company.com")} {...noAutofillProps} />
            </label>
            <label className="block space-y-1 text-sm text-foreground">
              <span>{t("Password")}</span>
              <Input type="password" value={newUserPassword} onChange={(e) => setNewUserPassword(e.target.value)} placeholder={t("Minimum 8 characters")} {...newCredentialProps} />
            </label>
            <label className="block space-y-1 text-sm text-foreground">
              <span>{t("Full name (optional)")}</span>
              <Input type="text" value={newUserFullName} onChange={(e) => setNewUserFullName(e.target.value)} placeholder={t("John Doe")} />
            </label>
            {newUserError && <Alert title={t("Error")}>{newUserError}</Alert>}
            <div className="flex justify-end">
              <Button type="submit" size="sm" disabled={createUserMutation.isPending}>{createUserMutation.isPending ? "..." : t("Create user")}</Button>
            </div>
          </form>

          {usersQuery.isLoading && <div className="text-sm text-muted-foreground">{t("Loading users...")}</div>}

          {usersQuery.data?.users && (
            <div className="space-y-1">
              {usersQuery.data.users.map((u: TenantUserResponse) => (
                <div key={u.id} className="flex items-center justify-between rounded border border-border px-3 py-2">
                  <div>
                    <span className="text-sm text-foreground">{u.email}</span>
                    <span className="ml-2 text-xs text-muted-foreground">({u.full_name})</span>
                  </div>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => { setReassignUserId(u.id); setReassignTargetTenantId(""); setReassignError(null); }}
                  >
                    {t("Remove")}
                  </Button>
                </div>
              ))}
              {usersQuery.data.users.length === 0 && (
                <div className="text-sm text-muted-foreground">{t("No users assigned")}</div>
              )}
            </div>
          )}
        </div>
      </Dialog>

      <Dialog open={Boolean(reassignUserId)} title={t("Reassign user")} onClose={() => setReassignUserId(null)}>
        <div className="space-y-3">
          <form onSubmit={(e) => {
            e.preventDefault();
            setReassignError(null);
            if (!reassignTargetTenantId.trim()) { setReassignError(t("Target tenant ID required")); return; }
            removeMutation.mutate({ tenantId: showUsers!.id, userId: reassignUserId!, targetTenantId: reassignTargetTenantId });
          }}>
            <label className="block space-y-1 text-sm text-foreground">
              <span>{t("Target Tenant ID")}</span>
              <Input type="text" value={reassignTargetTenantId} onChange={(e) => setReassignTargetTenantId(e.target.value)} placeholder={t("Destination tenant UUID")} autoFocus />
            </label>
            {reassignError && <Alert title={t("Error")}>{reassignError}</Alert>}
            <div className="flex justify-end gap-2 mt-3">
              <Button type="button" variant="secondary" size="sm" onClick={() => setReassignUserId(null)}>{t("Cancel")}</Button>
              <Button type="submit" size="sm" disabled={removeMutation.isPending}>{removeMutation.isPending ? "..." : t("Reassign")}</Button>
            </div>
          </form>
        </div>
      </Dialog>
    </section>
  );
}
