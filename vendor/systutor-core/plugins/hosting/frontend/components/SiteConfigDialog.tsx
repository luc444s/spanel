import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { Select } from "@systutor/shell/ui/select";
import { toast } from "@systutor/shell/ui/toast";

import { useT } from "@/i18n/use-i18n";

import {
  getHostingSiteConfig,
  hostingKeys,
  rotateHostingSiteSalts,
  updateHostingSiteConfig,
  type HostingSite,
} from "../api";

const BOOLEAN_KEYS = new Set(["WP_DEBUG", "WP_DEBUG_LOG", "WP_DEBUG_DISPLAY"]);
const CORE_KEYS = new Set(["WP_AUTO_UPDATE_CORE"]);

/** Only the values that changed and are not empty. Empty means "leave it". */
export function changedEnv(
  draft: Record<string, string>,
  original: Record<string, string>
): Record<string, string> {
  const changed: Record<string, string> = {};
  for (const [key, raw] of Object.entries(draft)) {
    const value = raw.trim();
    if (!value) continue;
    if (value !== (original[key] ?? "")) changed[key] = value;
  }
  return changed;
}

type SiteConfigDialogProps = {
  site: HostingSite | null;
  canManage: boolean;
  onClose: () => void;
};

/**
 * Site config on top of the site dialog. The server owns the whitelist, so the
 * inputs come from `editable_keys`: adding a key on the backend makes it
 * appear here without touching the client. Secret values are never returned,
 * so this component can only render their names.
 */
export function SiteConfigDialog({ site, canManage, onClose }: SiteConfigDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saltsOpen, setSaltsOpen] = useState(false);

  const noChangeOption = t("— no change —");
  const BOOLEAN_OPTIONS = [
    { value: "", label: noChangeOption },
    { value: "true", label: "true" },
    { value: "false", label: "false" },
  ];

  const CORE_OPTIONS = [
    { value: "", label: noChangeOption },
    { value: "true", label: "true" },
    { value: "false", label: "false" },
    { value: "minor", label: "minor" },
  ];

  const configQuery = useQuery({
    queryKey: hostingKeys.config(site?.id ?? ""),
    queryFn: () => getHostingSiteConfig(site!.id),
    enabled: Boolean(site),
  });

  useEffect(() => {
    if (configQuery.data) setDraft({ ...configQuery.data.editable_env });
  }, [configQuery.data]);

  const updateMutation = useMutation({
    mutationFn: ({ siteId, env }: { siteId: string; env: Record<string, string> }) =>
      updateHostingSiteConfig(siteId, env),
    onSuccess: async (_data, variables) => {
      await queryClient.invalidateQueries({ queryKey: hostingKeys.config(variables.siteId) });
      // Saving recreates the container, so the list's status must be refetched.
      await queryClient.invalidateQueries({ queryKey: hostingKeys.sites });
      toast.success(t("Configuration saved"));
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const saltsMutation = useMutation({
    mutationFn: (siteId: string) => rotateHostingSiteSalts(siteId),
    onSuccess: () => {
      setSaltsOpen(false);
      toast.success(t("Security keys rotated"));
    },
    onError: (error: Error) => {
      setSaltsOpen(false);
      toast.error(error.message);
    },
  });

  const config = configQuery.data;
  const original = config?.editable_env ?? {};
  const editable = canManage && Boolean(site?.managed);
  const changed = changedEnv(draft, original);
  const hasChanges = Object.keys(changed).length > 0;

  return (
    <Dialog
      open={Boolean(site)}
      title={t("Configuration for {site}", { site: site?.container_name ?? "" })}
      description={site?.domain ?? t("No domain")}
      onClose={onClose}
      zIndexClassName="z-[1100]"
      maxWidthClassName="max-w-2xl"
      actions={
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            {editable ? (
              <Button
                type="button"
                variant="secondary"
                disabled={saltsMutation.isPending}
                onClick={() => setSaltsOpen(true)}
              >
                {t("Rotate security keys")}
              </Button>
            ) : null}
          </div>
          <div className="flex gap-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              {t("Cancel")}
            </Button>
            {editable ? (
              <Button
                type="button"
                disabled={!hasChanges || updateMutation.isPending}
                onClick={() => {
                  if (!site) return;
                  updateMutation.mutate({ siteId: site.id, env: changed });
                }}
              >
                {updateMutation.isPending ? t("Saving...") : t("Save")}
              </Button>
            ) : null}
          </div>
        </div>
      }
    >
      {configQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">{t("Loading configuration...")}</p>
      ) : configQuery.error ? (
        <Alert title={t("Could not load configuration")}>
          {configQuery.error instanceof Error
            ? configQuery.error.message
            : t("Unknown error while querying the site.")}
        </Alert>
      ) : config ? (
        <div className="space-y-5">
          {editable ? (
            <Alert title={t("Saving recreates the container")}>
              {t("The site may take a few seconds to come back. Data and users are not lost.")}
            </Alert>
          ) : (
            <p className="text-sm text-muted-foreground">
              {site?.managed
                ? t("Read only: you do not have permission to edit it.")
                : t("Site not manageable: read only.")}
            </p>
          )}

          <section className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t("Editable variables")}
            </h3>
            <div className="grid gap-3 sm:grid-cols-2">
              {config.editable_keys.map((key) => (
                <label key={key} className="space-y-1 text-sm">
                  <span className="block font-mono text-xs text-muted-foreground">{key}</span>
                  {editable ? (
                    BOOLEAN_KEYS.has(key) || CORE_KEYS.has(key) ? (
                      <Select
                        value={draft[key] ?? ""}
                        onChange={(value) =>
                          setDraft((current) => ({ ...current, [key]: value }))
                        }
                        options={CORE_KEYS.has(key) ? CORE_OPTIONS : BOOLEAN_OPTIONS}
                        placeholder={t("— no change —")}
                      />
                    ) : (
                      <Input
                        value={draft[key] ?? ""}
                        placeholder={original[key] ? t("— no change —") : t("Not set")}
                        onChange={(event) =>
                          setDraft((current) => ({ ...current, [key]: event.target.value }))
                        }
                      />
                    )
                  ) : (
                    <p className="rounded-md border border-border bg-muted px-3 py-2 font-mono text-xs text-foreground">
                      {original[key] ?? "—"}
                    </p>
                  )}
                </label>
              ))}
            </div>
            {editable ? (
              <p className="text-xs text-muted-foreground">
                {t("Leaving a field empty keeps the current value.")}
              </p>
            ) : null}
          </section>

          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t("Secrets")}
            </h3>
            {config.secret_names.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("No secrets are registered.")}</p>
            ) : (
              <ul className="space-y-1">
                {config.secret_names.map((name) => (
                  <li
                    key={name}
                    className="flex items-center justify-between rounded-md border border-border bg-muted px-3 py-2 text-sm"
                  >
                    <span className="font-mono text-xs">🔒 {name}</span>
                    <span className="text-xs text-muted-foreground">{t("hidden value")}</span>
                  </li>
                ))}
              </ul>
            )}
            <p className="text-xs text-muted-foreground">{t("Values are never shown.")}</p>
          </section>
        </div>
      ) : null}

      <Dialog
        open={saltsOpen}
        title={t("Rotate WordPress security keys")}
        description={site?.container_name}
        onClose={() => setSaltsOpen(false)}
        zIndexClassName="z-[1200]"
        maxWidthClassName="max-w-md"
        actions={
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="secondary"
              disabled={saltsMutation.isPending}
              onClick={() => setSaltsOpen(false)}
            >
              {t("Cancel")}
            </Button>
            <Button
              type="button"
              disabled={saltsMutation.isPending || !site}
              onClick={() => {
                if (!site) return;
                saltsMutation.mutate(site.id);
              }}
            >
              {saltsMutation.isPending ? t("Processing...") : t("Rotate security keys")}
            </Button>
          </div>
        }
      >
        <p className="text-sm text-muted-foreground">
          {t("New security keys will be generated. All WordPress users will be logged out and will have to sign in again.")}
        </p>
      </Dialog>
    </Dialog>
  );
}
