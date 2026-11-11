import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";

import {
  CoreBranch,
  coreManagementKeys,
  createCoreBranch,
  disableCoreBranch,
  enableCoreBranch,
  invalidateCoreManagementKey,
  listCoreBranches,
  updateCoreBranch,
} from "../core-management/api";
import { useAuthStore } from "../auth/store";
import { Alert } from "@systutor/shell/ui/alert";
import { Badge } from "@systutor/shell/ui/badge";
import { Button } from "@systutor/shell/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@systutor/shell/ui/card";
import { DataTable } from "@systutor/shell/ui/data-table";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { useT } from "@/i18n/use-i18n";

type BranchFormState = {
  id?: string;
  name: string;
  code: string;
};

const EMPTY_BRANCH_FORM: BranchFormState = { name: "", code: "" };

export function BranchesPage() {
  const t = useT();
  const queryClient = useQueryClient();
  const permissions = useAuthStore((state) => state.permissions);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [formState, setFormState] = useState<BranchFormState>(EMPTY_BRANCH_FORM);
  const [formError, setFormError] = useState<string | null>(null);

  const branchesQuery = useQuery({
    queryKey: [...coreManagementKeys.branches],
    queryFn: listCoreBranches,
  });

  const canManage = permissions.includes("core.branches.manage");

  const saveBranchMutation = useMutation({
    mutationFn: async (payload: BranchFormState) => {
      if (payload.id) {
        return updateCoreBranch(payload.id, { name: payload.name, code: payload.code });
      }
      return createCoreBranch({ name: payload.name, code: payload.code });
    },
    onSuccess: async () => {
      await invalidateCoreManagementKey(queryClient, coreManagementKeys.branches);
      setIsDialogOpen(false);
      setFormState(EMPTY_BRANCH_FORM);
      setFormError(null);
    },
  });

  const toggleBranchMutation = useMutation({
    mutationFn: async ({ branchId, active }: { branchId: string; active: boolean }) => {
      return active ? disableCoreBranch(branchId) : enableCoreBranch(branchId);
    },
    onSuccess: async () => {
      await invalidateCoreManagementKey(queryClient, coreManagementKeys.branches);
    },
  });

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    try {
      await saveBranchMutation.mutateAsync(formState);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : t("Could not save the branch."));
    }
  }

  return (
    <BranchesPageContent
      branches={branchesQuery.data ?? []}
      canManage={canManage}
      hasError={Boolean(branchesQuery.error)}
      isDialogOpen={isDialogOpen}
      formState={formState}
      formError={formError}
      isSaving={saveBranchMutation.isPending}
      onCreate={() => {
        setFormState(EMPTY_BRANCH_FORM);
        setFormError(null);
        setIsDialogOpen(true);
      }}
      onEdit={(branch) => {
        setFormState({ id: branch.id, name: branch.name, code: branch.code });
        setFormError(null);
        setIsDialogOpen(true);
      }}
      onCloseDialog={() => setIsDialogOpen(false)}
      onSubmit={handleSubmit}
      onFieldChange={(value) => setFormState((current) => ({ ...current, ...value }))}
      onToggleBranch={(branch) => toggleBranchMutation.mutate({ branchId: branch.id, active: branch.active })}
    />
  );
}

type BranchesPageContentProps = {
  branches: CoreBranch[];
  canManage: boolean;
  hasError: boolean;
  isDialogOpen: boolean;
  formState: BranchFormState;
  formError: string | null;
  isSaving: boolean;
  onCreate: () => void;
  onEdit: (branch: CoreBranch) => void;
  onCloseDialog: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onFieldChange: (value: Partial<BranchFormState>) => void;
  onToggleBranch: (branch: CoreBranch) => void;
};

export function BranchesPageContent({
  branches,
  canManage,
  hasError,
  isDialogOpen,
  formState,
  formError,
  isSaving,
  onCreate,
  onEdit,
  onCloseDialog,
  onSubmit,
  onFieldChange,
  onToggleBranch,
}: BranchesPageContentProps) {
  const t = useT();
  return (
    <section className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold text-foreground">{t("Branches")}</h1>
          <p className="text-sm text-muted-foreground">{t("Tenant-aware administration of available branches.")}</p>
        </div>
        {canManage ? <Button onClick={onCreate}>{t("Create branch")}</Button> : null}
      </div>

      {hasError ? (
        <Alert title={t("Could not load branches")}>{t("Check permissions or connectivity.")}</Alert>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>{t("Branches")}</CardTitle>
          <CardDescription>{t("Minimal list of active or disabled branches.")}</CardDescription>
        </CardHeader>
        <CardContent>
          <DataTable
            columns={[
              { key: "name", header: t("Name"), render: (branch) => branch.name },
              { key: "code", header: t("Code"), render: (branch) => branch.code },
              { key: "status", header: t("Status"), render: (branch) => <Badge>{branch.active ? t("Active") : t("Inactive")}</Badge> },
              {
                key: "actions",
                header: t("Actions"),
                className: "w-56",
                render: (branch) => (
                  <div className="flex flex-wrap gap-2">
                    {canManage ? (
                      <Button variant="secondary" onClick={() => onEdit(branch)}>
                        {t("Edit")}
                      </Button>
                    ) : null}
                    {canManage ? (
                      <Button variant="secondary" onClick={() => onToggleBranch(branch)}>
                        {branch.active ? t("Deactivate") : t("Activate")}
                      </Button>
                    ) : null}
                  </div>
                ),
              },
            ]}
            rows={branches}
            rowKey={(branch) => branch.id}
            emptyMessage={t("No branches registered for this tenant.")}
          />
        </CardContent>
      </Card>

      <Dialog
        open={isDialogOpen}
        title={formState.id ? t("Edit branch") : t("Create branch")}
        description={t("Minimal core management form.")}
        onClose={onCloseDialog}
      >
        <form className="space-y-4" onSubmit={onSubmit}>
          <label className="block space-y-2 text-sm text-foreground">
            <span>{t("Name")}</span>
            <Input value={formState.name} onChange={(event) => onFieldChange({ name: event.target.value })} />
          </label>

          <label className="block space-y-2 text-sm text-foreground">
            <span>{t("Code")}</span>
            <Input value={formState.code} onChange={(event) => onFieldChange({ code: event.target.value.toUpperCase() })} />
          </label>

          {formError ? <Alert title={t("Could not save the branch")}>{formError}</Alert> : null}

          <div className="flex justify-end gap-3">
            <Button type="button" variant="secondary" onClick={onCloseDialog}>
              {t("Cancel")}
            </Button>
            <Button type="submit" disabled={isSaving}>
              {formState.id ? t("Save changes") : t("Create branch")}
            </Button>
          </div>
        </form>
      </Dialog>
    </section>
  );
}
