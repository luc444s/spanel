import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";

import { useAuthStore } from "../../../../../../apps/web/src/features/auth/store";
import { apiRequest } from "@systutor/shell/api/client";
import {
  createDomain,
  domainsKeys,
  getCertificate,
  listDomains,
  removeDomain,
  type CertificateInfo,
  type DomainItem,
} from "../api";
import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { toast } from "@systutor/shell/ui/toast";
import { CertificatesDialog } from "../components/CertificatesDialog";

function certificateLabel(info: CertificateInfo | undefined) {
  if (!info) return "Sin datos";
  if (info.state === "active" && info.verified) return "Válido";
  if (info.state === "pending_dns") return "Sin DNS";
  return "Pendiente";
}

function certificateTone(info: CertificateInfo | undefined) {
  if (!info) return "text-muted-foreground";
  if (info.state === "active" && info.verified) return "text-emerald-600";
  return "text-amber-600";
}

function CertificateCell({ domain }: { domain: string }) {
  const query = useQuery({
    queryKey: domainsKeys.certificate(domain),
    queryFn: () => getCertificate(domain),
    enabled: false,
    retry: false,
    staleTime: 60_000,
  });

  const info = query.data;
  const tooltip = info
    ? [info.issuer, info.not_after, info.error].filter(Boolean).join(" · ")
    : "Todavía no se verificó";

  return (
    <div className="flex items-center gap-2">
      <span className={`text-sm ${certificateTone(info)}`} title={tooltip}>
        {certificateLabel(info)}
      </span>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        onClick={() => void query.refetch()}
        disabled={query.isFetching}
      >
        {query.isFetching ? "..." : "Verificar"}
      </Button>
    </div>
  );
}

function countMailboxes(domain: string, accounts: string[] | undefined, tenantDomain: string) {
  if (!accounts) return "—";
  if (tenantDomain !== "all" && domain.toLowerCase() !== tenantDomain.toLowerCase()) {
    return "—";
  }
  return String(
    accounts.filter((email) => email.toLowerCase().endsWith(`@${domain.toLowerCase()}`)).length
  );
}

export default function DomainsPage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((state) => state.permissions);
  const canManage = permissions.includes("domains.domains.manage");
  const canReadMail = permissions.includes("mail.accounts.read");
  const canReadCertificates = permissions.includes("domains.certificates.read");
  const canManageCertificates = permissions.includes("domains.certificates.manage");

  const domainsQuery = useQuery({ queryKey: domainsKeys.list, queryFn: listDomains });
  const mailQuery = useQuery({
    queryKey: ["mail", "accounts"],
    queryFn: () =>
      apiRequest<{ domain: string; accounts: string[] }>(
        "/api/v1/plugins/mail/mail/accounts"
      ),
    enabled: canReadMail,
    staleTime: 60_000,
    retry: false,
  });

  const [addOpen, setAddOpen] = useState(false);
  const [siteId, setSiteId] = useState("");
  const [domain, setDomain] = useState("");
  const [www, setWww] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<DomainItem | null>(null);
  const [certificatesOpen, setCertificatesOpen] = useState(false);
  const [certificatesDomain, setCertificatesDomain] = useState<string | null>(null);

  const items = domainsQuery.data?.items ?? [];
  const sites = domainsQuery.data?.sites ?? [];

  const createMutation = useMutation({
    mutationFn: createDomain,
    onSuccess: () => {
      toast.success("Dominio agregado");
      void queryClient.invalidateQueries({ queryKey: domainsKeys.list });
      setAddOpen(false);
      setDomain("");
      setWww(false);
    },
    onError: (error: Error) => setFormError(error.message),
  });

  const removeMutation = useMutation({
    mutationFn: removeDomain,
    onSuccess: () => {
      toast.success("Dominio quitado");
      void queryClient.invalidateQueries({ queryKey: domainsKeys.list });
      setRemoving(null);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    if (!siteId) {
      setFormError("Elegí un sitio");
      return;
    }
    createMutation.mutate({ site_id: siteId, domain: domain.trim(), www });
  }

  function openAdd() {
    setFormError(null);
    setSiteId(sites[0]?.site_id ?? "");
    setDomain("");
    setWww(false);
    setAddOpen(true);
  }

  return (
    <section className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-foreground">Dominios</h1>
          <p className="text-sm text-muted-foreground">
            El dominio primario lo gestiona Hosting. Los extras se publican por el proxy.
            Doble clic en una fila para ver sus certificados TLS.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canManage && (
            <Button size="sm" onClick={openAdd} disabled={sites.length === 0}>
              Agregar dominio
            </Button>
          )}
          {canReadCertificates && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setCertificatesDomain(null);
                setCertificatesOpen(true);
              }}
            >
              Certificados TLS
            </Button>
          )}
        </div>
      </div>

      {domainsQuery.isError && (
        <Alert title="No se pudieron cargar los dominios">
          {(domainsQuery.error as Error).message}
        </Alert>
      )}

      {domainsQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Cargando dominios...</p>
      ) : items.length === 0 ? (
        <p
          className="rounded-md border border-border bg-card px-3 py-4 text-sm
            text-muted-foreground"
        >
          Todavía no hay dominios. Creá un sitio en Hosting o agregá un dominio extra.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full min-w-[720px] border-collapse text-sm">
            <thead className="bg-muted text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-3 py-2">Dominio</th>
                <th className="px-3 py-2">Sitio</th>
                <th className="px-3 py-2">Dueño</th>
                <th className="px-3 py-2">Certificado</th>
                {canReadMail && <th className="px-3 py-2">Buzones</th>}
                <th className="px-3 py-2 text-right">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const extraConflict = item.conflict;
                return (
                  <tr
                    key={item.domain}
                    className="cursor-pointer border-t border-border hover:bg-accent/30"
                    title="Doble clic para ver los certificados TLS"
                    onDoubleClick={() => {
                      setCertificatesDomain(item.domain);
                      setCertificatesOpen(true);
                    }}
                  >
                    <td className="px-3 py-2 font-medium text-foreground">
                      {item.domain}
                      {item.outside_tenant && (
                        <span
                          className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs
                            text-amber-800"
                          title="Fuera del sufijo de sitios del tenant"
                        >
                          Fuera del tenant
                        </span>
                      )}
                      {extraConflict && (
                        <span
                          className="ml-2 rounded bg-red-100 px-1.5 py-0.5 text-xs
                            text-red-700"
                        >
                          Dos dueños
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {item.container ?? "—"}
                    </td>
                    <td className="px-3 py-2">
                      {item.owner === "hosting" ? (
                        <span className="text-muted-foreground" title="Gestionado por Hosting">
                          Hosting 🔒
                        </span>
                      ) : (
                        <span className="text-foreground">Plugin</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <CertificateCell domain={item.domain} />
                    </td>
                    {canReadMail && (
                      <td
                        className="px-3 py-2 text-muted-foreground"
                        title={
                          mailQuery.data && mailQuery.data.domain !== "all" &&
                          item.domain.toLowerCase() !== mailQuery.data.domain.toLowerCase()
                            ? "El correo solo existe en el dominio del tenant"
                            : undefined
                        }
                      >
                        {countMailboxes(
                          item.domain,
                          mailQuery.data?.accounts,
                          mailQuery.data?.domain ?? ""
                        )}
                      </td>
                    )}
                    <td className="px-3 py-2" onDoubleClick={(event) => event.stopPropagation()}>
                      <div className="flex flex-wrap items-center justify-end gap-2">
                        {item.owner === "domains" && canManage ? (
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => setRemoving(item)}
                          >
                            Quitar
                          </Button>
                        ) : extraConflict && canManage ? (
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => setRemoving(item)}
                          >
                            Quitar extra
                          </Button>
                        ) : (
                          <span className="text-xs text-muted-foreground">
                            Gestionado por Hosting
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <Dialog open={addOpen} title="Agregar dominio" onClose={() => setAddOpen(false)}>
        <form className="space-y-3" onSubmit={submit}>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Sitio</span>
            <select
              className="w-full rounded-md border border-input bg-background px-2 py-2 text-sm"
              value={siteId}
              onChange={(event) => setSiteId(event.target.value)}
            >
              {sites.map((site) => (
                <option key={site.site_id} value={site.site_id}>
                  {site.container}
                  {site.primary_domain ? ` (${site.primary_domain})` : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Dominio</span>
            <Input
              value={domain}
              onChange={(event) => setDomain(event.target.value)}
              placeholder="ejemplo.spanel"
              autoFocus
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={www}
              onChange={(event) => setWww(event.target.checked)}
            />
            <span>Agregar también www.</span>
          </label>
          {formError && <Alert title="No se pudo agregar">{formError}</Alert>}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => setAddOpen(false)}
            >
              Cancelar
            </Button>
            <Button type="submit" size="sm" disabled={createMutation.isPending}>
              Agregar
            </Button>
          </div>
        </form>
      </Dialog>

      <Dialog
        open={Boolean(removing)}
        title="Quitar dominio"
        description={removing?.domain}
        onClose={() => setRemoving(null)}
      >
        <p className="text-sm text-muted-foreground">
          Se elimina la ruta del proxy. El sitio y su dominio primario no cambian.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={() => setRemoving(null)}>
            Cancelar
          </Button>
          <Button
            size="sm"
            disabled={removeMutation.isPending}
            onClick={() => removing && removeMutation.mutate(removing.domain)}
          >
            Quitar
          </Button>
        </div>
      </Dialog>

      <CertificatesDialog
        open={certificatesOpen && canReadCertificates}
        canManage={canManageCertificates}
        visibleDomains={items.map((item) => item.domain)}
        onlyHostname={certificatesDomain ? `mail.${certificatesDomain}` : null}
        highlightHostname={
          certificatesDomain ? `mail.${certificatesDomain}` : null
        }
        onClose={() => {
          setCertificatesOpen(false);
          setCertificatesDomain(null);
        }}
      />
    </section>
  );
}
