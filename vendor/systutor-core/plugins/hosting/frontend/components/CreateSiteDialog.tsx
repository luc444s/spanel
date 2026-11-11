import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

import {
  checkHostingDomain,
  createHostingSite,
  hostingKeys,
  type HostingSiteKind,
  type StaticProfile,
} from "../api";
import { Select } from "@systutor/shell/ui/select";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { toast } from "@systutor/shell/ui/toast";
import { Input } from "@systutor/shell/ui/input";

import { t } from "@/i18n";
import { useT } from "@/i18n/use-i18n";

type DomainCheckState =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "available"; suffix: string | null }
  | { status: "unavailable"; reason: string }
  | { status: "error"; message: string };

/** Detail line of one profile, exactly what the admin curated. */
function profileDetail(profile: StaticProfile) {
  const env =
    profile.env_keys.length > 0
      ? ` · ${t("vars")}: ${profile.env_keys.join(", ")}`
      : "";
  return `${profile.image} · :${profile.container_port} · ${profile.web_root} · ${profile.health_mode}${env}`;
}

function DomainCheckMessage({ check }: { check: DomainCheckState }) {
  const t = useT();
  if (check.status === "idle") return null;
  if (check.status === "checking") {
    return <p className="text-xs text-muted-foreground">{t("Validating domain...")}</p>;
  }
  if (check.status === "available") {
    return (
      <p className="text-xs text-emerald-600">
        {check.suffix
          ? t("Domain available for suffix {suffix}", { suffix: check.suffix })
          : t("Domain available")}
      </p>
    );
  }
  if (check.status === "unavailable") {
    return <p className="text-xs text-destructive">{check.reason}</p>;
  }
  return (
    <p className="text-xs text-destructive">
      {t("Could not validate the domain: {message}", { message: check.message })}
    </p>
  );
}

type CreateSiteDialogProps = {
  open: boolean;
  /** Profiles come from GET /hosting/static-profiles (hosting.sites.read). */
  profiles: StaticProfile[];
  onClose: () => void;
  onCreated: (result: {
    kind: HostingSiteKind;
    domain: string;
    adminPassword: string | null;
  }) => void;
};

/**
 * A.SPEC 0042: the profile selector exists ONLY with `kind=static`; with
 * wordpress the select does not exist and the payload carries no profile.
 */
export function CreateSiteDialog({
  open,
  profiles,
  onClose,
  onCreated,
}: CreateSiteDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<HostingSiteKind>("wordpress");
  const [profileId, setProfileId] = useState("nginx");
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [domainCheck, setDomainCheck] = useState<DomainCheckState>({ status: "idle" });

  const KIND_OPTIONS = [
    { value: "wordpress", label: t("WordPress") },
    { value: "static", label: t("Static") },
  ];

  const selected = profiles.find((p) => p.id === profileId) ?? null;

  // The profile list may load after the dialog opens; keep a valid id.
  useEffect(() => {
    if (profiles.length > 0 && !profiles.some((p) => p.id === profileId)) {
      setProfileId(profiles[0].id);
    }
  }, [profiles, profileId]);

  useEffect(() => {
    if (!open) return;
    const value = domain.trim();
    if (value.length < 4) {
      setDomainCheck({ status: "idle" });
      return;
    }
    setDomainCheck({ status: "checking" });
    let cancelled = false;
    const timer = window.setTimeout(() => {
      checkHostingDomain(value)
        .then((result) => {
          if (cancelled) return;
          if (result.available) {
            setDomainCheck({ status: "available", suffix: result.suffix });
          } else {
            setDomainCheck({
              status: "unavailable",
              reason: result.reason ?? t("The domain is not available"),
            });
          }
        })
        .catch((error: Error) => {
          if (cancelled) return;
          setDomainCheck({
            status: "error",
            message: error.message || t("Could not validate the domain"),
          });
        });
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [domain, open]);

  function reset() {
    setKind("wordpress");
    setProfileId("nginx");
    setName("");
    setDomain("");
    setDomainCheck({ status: "idle" });
  }

  const createMutation = useMutation({
    mutationFn: createHostingSite,
    onSuccess: async (data) => {
      reset();
      onCreated({
        kind: data.site.kind,
        domain: data.site.domain ?? domain,
        adminPassword: data.admin_password,
      });
      // La lista se refresca detrás del usuario (A.SPEC 0043).
      await queryClient.invalidateQueries({ queryKey: hostingKeys.sites });
    },
  });

  /**
   * A.SPEC 0043: the dialog closes at the submit instant; the POST keeps
   * running in background (react-query) and toast.promise shows the
   * pending/final states. Errors always surface the backend message.
   */
  function submitSite(event: FormEvent) {
    event.preventDefault();
    onClose();
    toast.promise(
      createMutation.mutateAsync({
        kind,
        name: name.trim(),
        domain: domain.trim(),
        ...(kind === "static" ? { profile: profileId } : {}),
      }),
      {
        loading: t("Creating site…"),
        success: (data) =>
          data.site.domain
            ? t("Site created ({domain})", { domain: data.site.domain })
            : t("Site created"),
        error: (error: Error) => error.message || t("Could not create the site"),
      },
    );
  }

  return (
    <Dialog open={open} title={t("Create site")} onClose={onClose}>
      <form
        className="space-y-3"
        onSubmit={submitSite}
      >
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">{t("Type")}</label>
          <Select
            value={kind}
            onChange={(value) => setKind(value as HostingSiteKind)}
            options={KIND_OPTIONS}
          />
        </div>
        {kind === "static" ? (
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">{t("Profile")}</label>
            {profiles.length === 0 ? (
              <p className="text-xs text-muted-foreground">{t("No profiles loaded yet.")}</p>
            ) : (
              <Select
                value={profileId}
                onChange={setProfileId}
                options={profiles.map((p) => ({ value: p.id, label: p.name }))}
              />
            )}
            {selected ? (
              <p className="text-xs text-muted-foreground">{profileDetail(selected)}</p>
            ) : null}
          </div>
        ) : null}
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">{t("Name")}</label>
          <Input
            required
            placeholder={t("Name")}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">{t("Domain")}</label>
          <Input
            required
            placeholder={t("Domain")}
            value={domain}
            onChange={(event) => setDomain(event.target.value)}
          />
          <DomainCheckMessage check={domainCheck} />
        </div>
        <Button
          type="submit"
          disabled={
            createMutation.isPending ||
            name.trim().length === 0 ||
            domainCheck.status !== "available" ||
            (kind === "static" && !selected)
          }
        >
          {createMutation.isPending ? t("Creating...") : t("Create")}
        </Button>
      </form>
    </Dialog>
  );
}
