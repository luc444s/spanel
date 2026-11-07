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

type DomainCheckState =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "available"; suffix: string | null }
  | { status: "unavailable"; reason: string }
  | { status: "error"; message: string };

const KIND_OPTIONS = [
  { value: "wordpress", label: "WordPress" },
  { value: "static", label: "Estático" },
];

/** Detail line of one profile, exactly what the admin curated. */
function profileDetail(profile: StaticProfile) {
  const env =
    profile.env_keys.length > 0 ? ` · vars: ${profile.env_keys.join(", ")}` : "";
  return `${profile.image} · :${profile.container_port} · ${profile.web_root} · ${profile.health_mode}${env}`;
}

function DomainCheckMessage({ check }: { check: DomainCheckState }) {
  if (check.status === "idle") return null;
  if (check.status === "checking") {
    return <p className="text-xs text-muted-foreground">Validando dominio...</p>;
  }
  if (check.status === "available") {
    return (
      <p className="text-xs text-emerald-600">
        Dominio disponible{check.suffix ? ` para el sufijo ${check.suffix}` : ""}
      </p>
    );
  }
  if (check.status === "unavailable") {
    return <p className="text-xs text-destructive">{check.reason}</p>;
  }
  return <p className="text-xs text-destructive">No se pudo validar el dominio: {check.message}</p>;
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
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<HostingSiteKind>("wordpress");
  const [profileId, setProfileId] = useState("nginx");
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [domainCheck, setDomainCheck] = useState<DomainCheckState>({ status: "idle" });

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
              reason: result.reason ?? "El dominio no está disponible",
            });
          }
        })
        .catch((error: Error) => {
          if (cancelled) return;
          setDomainCheck({
            status: "error",
            message: error.message || "No se pudo validar el dominio",
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
        loading: "Creando sitio…",
        success: (data) =>
          data.site.domain ? `Sitio creado (${data.site.domain})` : "Sitio creado",
        error: (error: Error) => error.message || "No se pudo crear el sitio",
      },
    );
  }

  return (
    <Dialog open={open} title="Crear sitio" onClose={onClose}>
      <form
        className="space-y-3"
        onSubmit={submitSite}
      >
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Tipo</label>
          <Select
            value={kind}
            onChange={(value) => setKind(value as HostingSiteKind)}
            options={KIND_OPTIONS}
          />
        </div>
        {kind === "static" ? (
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Perfil</label>
            {profiles.length === 0 ? (
              <p className="text-xs text-muted-foreground">No hay perfiles cargados aún.</p>
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
          <label className="text-xs text-muted-foreground">Nombre</label>
          <Input
            required
            placeholder="Nombre"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Dominio</label>
          <Input
            required
            placeholder="Dominio"
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
          {createMutation.isPending ? "Creando..." : "Crear"}
        </Button>
      </form>
    </Dialog>
  );
}
