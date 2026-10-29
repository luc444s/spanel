import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";

import { useAuthStore } from "../../../../../../apps/web/src/features/auth/store";
import { apiRequest } from "@systutor/shell/api/client";
import {
  createDomain,
  domainsKeys,
  getCertificate,
  linkDomain,
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

const RESERVAR = "";

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

function CertificateCell({ domain, published }: { domain: string; published: boolean }) {
  const query = useQuery({
    queryKey: domainsKeys.certificate(domain),
    queryFn: () => getCertificate(domain),
    enabled: false,
    retry: false,
    staleTime: 60_000,
  });

  // Un dominio reservado no tiene ruta ni certificado: no se consulta la red.
  if (!published) {
    return (
      <span className="text-sm text-muted-foreground" title="Dominio reservado sin publicar">
        Sin publicar
      </span>
    );
  }

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
  const [siteId, setSiteId] = useState(RESERVAR);
  const [domain, setDomain] = useState("");
  const [www, setWww] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [linking, setLinking] = useState<DomainItem | null>(null);
  const [linkSiteId, setLinkSiteId] = useState("");
  const [removing, setRemoving] = useState<DomainItem | null>(null);

  const items = domainsQuery.data?.items ?? [];
  const sites = domainsQuery.data?.sites ?? [];
  const selectedSite = sites.find((site) => site.site_id === siteId);
  const linkTarget
    = sites.find((site) => site.site_id === linkSiteId) ?? sites[0];

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

  const linkMutation = useMutation({
    mutationFn: (payload: { domain: string; site_id: string; www: boolean }) =>
      linkDomain(payload.domain, {
        site_id: payload.site_id,
        www: payload.www,
      }),
    onSuccess: () => {
      toast.success("Dominio vinculado");
      void queryClient.invalidateQueries({ queryKey: domainsKeys.list });
      setLinking(null);
    },
    onError: (error: Error) => toast.error(error.message),
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
    createMutation.mutate({
      site_id: siteId || null,
      domain: domain.trim(),
      www,
    });
  }

  function openAdd() {
    setFormError(null);
    setSiteId(RESERVAR);
    setDomain("");
    setWww(false);
    setAddOpen(true);
  }

  function openLink(item: DomainItem) {
    setLinkSiteId(sites[0]?.site_id ?? "");
    setWww(false);
    setLinking(item);
  }

  return (
    <section className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-foreground">Dominios</h1>
        </div>
        {canManage && <Button size="sm" onClick={openAdd}>Agregar dominio</Button>}
      </div>

      {domainsQuery.isError && (
        <Alert title="No se pudieron cargar los dominios">
          {(domainsQuery.error as Error).message}
        </Alert>
      )}

      {domainsQuery.data && domainsQuery.data.proxy_available === false && (
        <Alert title="El proxy no responde">
          {domainsQuery.data.proxy_error ??
            "Se muestran solo los dominios con propiedad registrada; faltan las rutas publicadas."}
        </Alert>
      )}

      {domainsQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Cargando dominios...</p>
      ) : items.length === 0 ? (
        <p
          className="rounded-md border border-border bg-card px-3 py-4 text-sm
            text-muted-foreground"
        >
          {sites.length === 0
            ? "Todavía no hay dominios. Podés reservar uno sin sitio y vincularlo después."
            : "Todavía no hay dominios. Agregá uno para publicarlo en este tenant."}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full min-w-[760px] border-collapse text-sm">
            <thead className="bg-muted text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-3 py-2">Dominio</th>
                <th className="px-3 py-2">Sitio</th>
                <th className="px-3 py-2">Estado</th>
                <th className="px-3 py-2">Dueño</th>
                <th className="px-3 py-2">Certificado</th>
                {canReadMail && <th className="px-3 py-2">Buzones</th>}
                <th className="px-3 py-2 text-right">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const extraConflict = item.conflict;
                const published = item.published ?? true;
                return (
                  <tr key={item.domain} className="border-t border-border">
                    <td className="px-3 py-2 font-medium text-foreground">
                      {item.domain}
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
                      {published ? (
                        <span className="text-muted-foreground">Publicado</span>
                      ) : (
                        <span className="text-amber-600">Sin publicar</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {item.owner === "hosting" ? (
                        <span className="text-muted-foreground" title="Gestionado por Hosting">
                          Hosting 🔒
                        </span>
                      ) : published ? (
                        <span className="text-foreground">Extra</span>
                      ) : (
                        <span className="text-foreground">Dominio</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <CertificateCell domain={item.domain} published={published} />
                    </td>
                    {canReadMail && (
                      <td
                        className="px-3 py-2 text-muted-foreground"
                        title={
                          mailQuery.data && mailQuery.data.domain !== "all" &&
                          item.domain.toLowerCase() !== mailQuery.data.domain.toLowerCase()
                            ? "El correo también existe en dominios del tenant"
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
                    <td className="px-3 py-2 text-right">
                      {item.owner === "domains" && canManage && (
                        <span className="inline-flex gap-2">
                          {!published && sites.length > 0 && (
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => openLink(item)}
                            >
                              Vincular
                            </Button>
                          )}
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => setRemoving(item)}
                          >
                            Quitar
                          </Button>
                        </span>
                      )}
                      {extraConflict && canManage && item.owner !== "domains" && (
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => setRemoving(item)}
                        >
                          Quitar extra
                        </Button>
                      )}
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
            <span>Dominio</span>
            <Input
              value={domain}
              onChange={(event) => setDomain(event.target.value)}
              placeholder="ejemplo.com"
              autoFocus
            />
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>Destino</span>
            <select
              className="w-full rounded-md border border-input bg-background px-2 py-2 text-sm"
              value={siteId}
              onChange={(event) => setSiteId(event.target.value)}
            >
              <option value={RESERVAR}>Solo reservar (sin publicar)</option>
              {sites.map((site) => (
                <option key={site.site_id} value={site.site_id}>
                  {site.container}
                  {site.primary_domain ? ` (${site.primary_domain})` : ""}
                </option>
              ))}
            </select>
            <span className="text-xs text-muted-foreground">
              {selectedSite
                ? `El dominio se publicará en ${selectedSite.container}. Se creará la ruta en Traefik y se solicitará el certificado.`
                : "Reserva el dominio para este tenant. No se publica en Traefik ni se emite certificado hasta que lo vincules a un sitio."}
            </span>
          </label>
          <label className="flex items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={www}
              onChange={(event) => setWww(event.target.checked)}
            />
            <span>Agregar también www.{domain.trim() || "dominio"}</span>
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
        open={Boolean(linking)}
        title="Vincular dominio"
        description={linking?.domain}
        onClose={() => setLinking(null)}
      >
        <p className="text-sm text-muted-foreground">
          El dominio se publicará en {linkTarget?.container ?? "el sitio elegido"}.
        </p>
        <label className="mt-3 block space-y-1 text-sm text-foreground">
          <span>Sitio</span>
          <select
            className="w-full rounded-md border border-input bg-background px-2 py-2 text-sm"
            value={linkTarget?.site_id ?? ""}
            onChange={(event) => setLinkSiteId(event.target.value)}
          >
            {sites.map((site) => (
              <option key={site.site_id} value={site.site_id}>
                {site.container}
                {site.primary_domain ? ` (${site.primary_domain})` : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="mt-3 flex items-center gap-2 text-sm text-foreground">
          <input
            type="checkbox"
            checked={www}
            onChange={(event) => setWww(event.target.checked)}
          />
          <span>Agregar también www.{linking?.domain ?? "dominio"}</span>
        </label>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={() => setLinking(null)}>
            Cancelar
          </Button>
          <Button
            size="sm"
            disabled={linkMutation.isPending || !linkTarget}
            onClick={() =>
              linking
                && linkTarget
                && linkMutation.mutate({
                  domain: linking.domain,
                  site_id: linkTarget.site_id,
                  www,
                })
            }
          >
            Vincular
          </Button>
        </div>
      </Dialog>

      <Dialog
        open={Boolean(removing)}
        title="Quitar dominio"
        description={removing?.domain}
        onClose={() => setRemoving(null)}
      >
        <p className="text-sm text-muted-foreground">
          {(removing?.published ?? true)
            ? "Se elimina la ruta del proxy. El sitio y su dominio primario no cambian."
            : "Se libera la reserva del dominio. No hay ruta publicada ni certificado."}
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
    </section>
  );
}
