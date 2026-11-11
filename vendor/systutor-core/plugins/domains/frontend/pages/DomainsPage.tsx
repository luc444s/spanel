import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";

import { useAuthStore } from "../../../../../../apps/web/src/features/auth/store";
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
import { t } from "@/i18n";

const RESERVAR = "";

function certificateLabel(info: CertificateInfo | undefined) {
  if (!info) return t("No data");
  if (info.state === "active" && info.verified) return t("Valid");
  if (info.state === "pending_dns") return t("No DNS");
  return t("Pending");
}

function certificateTone(info: CertificateInfo | undefined) {
  if (!info) return "text-muted-foreground";
  if (info.state === "active" && info.verified) return "text-emerald-600";
  return "text-amber-600";
}

function CertificateCell({
  domain,
  published,
  readable = true,
}: {
  domain: string;
  published: boolean;
  readable?: boolean;
}) {
  const query = useQuery({
    queryKey: domainsKeys.certificate(domain),
    queryFn: () => getCertificate(domain),
    enabled: false,
    retry: false,
    staleTime: 60_000,
  });

  // Un dominio reservado no tiene ruta ni certificado: no se consulta la red.
  if (!published || !readable) {
    return (
      <span className="text-sm text-muted-foreground" title={t("Reserved domain, not published")}>
        {readable ? t("Not published") : t("Read only")}
      </span>
    );
  }

  const info = query.data;
  const tooltip = info
    ? [info.issuer, info.not_after, info.error].filter(Boolean).join(" · ")
    : t("Not verified yet");

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
        {query.isFetching ? "..." : t("Verify")}
      </Button>
    </div>
  );
}

export default function DomainsPage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((state) => state.permissions);
  const currentTenant = useAuthStore((state) => state.currentTenant);
  const platform = permissions.includes("core.platform.admin");
  const canManage = permissions.includes("domains.domains.manage");

  const domainsQuery = useQuery({
    queryKey: [...domainsKeys.list, platform ? "all" : "tenant"],
    queryFn: () => listDomains(platform),
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
  const ownSites = sites.filter(
    (site) => !site.tenant_id || site.tenant_id === currentTenant?.id
  );
  const selectedSite = ownSites.find((site) => site.site_id === siteId);
  const linkTarget
    = ownSites.find((site) => site.site_id === linkSiteId) ?? ownSites[0];

  const createMutation = useMutation({
    mutationFn: createDomain,
    onSuccess: () => {
      toast.success(t("Domain added"));
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
      toast.success(t("Domain linked"));
      void queryClient.invalidateQueries({ queryKey: domainsKeys.list });
      setLinking(null);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const removeMutation = useMutation({
    mutationFn: removeDomain,
    onSuccess: () => {
      toast.success(t("Domain removed"));
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
    setLinkSiteId(ownSites[0]?.site_id ?? "");
    setWww(false);
    setLinking(item);
  }

  return (
    <section className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-foreground">{t("Domains")}</h1>
        </div>
        {canManage && <Button size="sm" onClick={openAdd}>{t("Add domain")}</Button>}
      </div>

      {domainsQuery.isError && (
        <Alert title={t("Could not load domains")}>
          {(domainsQuery.error as Error).message}
        </Alert>
      )}

      {domainsQuery.data && domainsQuery.data.proxy_available === false && (
        <Alert title={t("The proxy is not responding")}>
          {domainsQuery.data.proxy_error ??
            t("Only domains with registered ownership are shown; published routes are missing.")}
        </Alert>
      )}

      {domainsQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">{t("Loading domains...")}</p>
      ) : items.length === 0 ? (
        <p
          className="rounded-md border border-border bg-card px-3 py-4 text-sm
            text-muted-foreground"
        >
          {ownSites.length === 0
            ? t("No domains yet. You can reserve one without a site and link it later.")
            : t("No domains yet. Add one to publish it in this tenant.")}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full min-w-[640px] border-collapse text-sm">
            <thead className="bg-muted text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-3 py-2">{t("Domain")}</th>
                {platform && <th className="px-3 py-2">{t("Tenant")}</th>}
                <th className="px-3 py-2">{t("Status")}</th>
                <th className="px-3 py-2">{t("Owner")}</th>
                <th className="px-3 py-2">{t("Certificate")}</th>
                <th className="px-3 py-2 text-right">{t("Actions")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const extraConflict = item.conflict;
                const published = item.published ?? true;
                const isOwned = !item.tenant_id || item.tenant_id === currentTenant?.id;
                return (
                  <tr key={item.domain} className="border-t border-border">
                    <td className="px-3 py-2 font-medium text-foreground">
                      {item.domain}
                      {extraConflict && (
                        <span
                          className="ml-2 rounded bg-red-100 px-1.5 py-0.5 text-xs
                            text-red-700"
                        >
                          {t("Two owners")}
                        </span>
                      )}
                    </td>
                    {platform && (
                      <td className="px-3 py-2 text-muted-foreground">
                        {item.tenant_name ?? item.tenant_id ?? "—"}
                      </td>
                    )}
                    <td className="px-3 py-2">
                      {published ? (
                        <span className="text-muted-foreground">{t("Published")}</span>
                      ) : (
                        <span className="text-amber-600">{t("Not published")}</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {item.owner === "hosting" ? (
                        <span className="text-muted-foreground" title={t("Managed by Hosting")}>
                          {t("Hosting")} 🔒
                        </span>
                      ) : published ? (
                        <span className="text-foreground">{t("Extra")}</span>
                      ) : (
                        <span className="text-foreground">{t("Domain")}</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <CertificateCell
                        domain={item.domain}
                        published={published}
                        readable={isOwned}
                      />
                    </td>
                    <td className="px-3 py-2 text-right">
                      {item.owner === "domains" && canManage && isOwned && item.editable && (
                        <span className="inline-flex gap-2">
                          {!published && ownSites.length > 0 && (
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => openLink(item)}
                            >
                              {t("Link")}
                            </Button>
                          )}
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => setRemoving(item)}
                          >
                            {t("Remove")}
                          </Button>
                        </span>
                      )}
                      {extraConflict && canManage && isOwned && item.owner !== "domains" && (
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => setRemoving(item)}
                        >
                          {t("Remove extra")}
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

      <Dialog open={addOpen} title={t("Add domain")} onClose={() => setAddOpen(false)}>
        <form className="space-y-3" onSubmit={submit}>
          <label className="block space-y-1 text-sm text-foreground">
            <span>{t("Domain")}</span>
            <Input
              value={domain}
              onChange={(event) => setDomain(event.target.value)}
              placeholder={t("example.com")}
              autoFocus
            />
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>{t("Destination")}</span>
            <select
              className="w-full rounded-md border border-input bg-background px-2 py-2 text-sm"
              value={siteId}
              onChange={(event) => setSiteId(event.target.value)}
            >
              <option value={RESERVAR}>{t("Reserve only (do not publish)")}</option>
              {ownSites.map((site) => (
                <option key={site.site_id} value={site.site_id}>
                  {site.container}
                  {site.primary_domain ? ` (${site.primary_domain})` : ""}
                </option>
              ))}
            </select>
            <span className="text-xs text-muted-foreground">
              {selectedSite
                ? t("The domain will be published on {container}. The Traefik route will be created and the certificate requested.", { container: selectedSite.container })
                : t("Reserve the domain for this tenant. It is not published in Traefik and no certificate is issued until you link it to a site.")}
            </span>
          </label>
          <label className="flex items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={www}
              onChange={(event) => setWww(event.target.checked)}
            />
            <span>{t("Also add www.{domain}", { domain: domain.trim() || t("domain") })}</span>
          </label>
          {formError && <Alert title={t("Could not add")}>{formError}</Alert>}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => setAddOpen(false)}
            >
              {t("Cancel")}
            </Button>
            <Button type="submit" size="sm" disabled={createMutation.isPending}>
              {t("Add")}
            </Button>
          </div>
        </form>
      </Dialog>

      <Dialog
        open={Boolean(linking)}
        title={t("Link domain")}
        description={linking?.domain}
        onClose={() => setLinking(null)}
      >
        <p className="text-sm text-muted-foreground">
          {t("The domain will be published on {container}.", { container: linkTarget?.container ?? t("the chosen site") })}
        </p>
        <label className="mt-3 block space-y-1 text-sm text-foreground">
          <span>{t("Site")}</span>
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
          <span>{t("Also add www.{domain}", { domain: linking?.domain ?? t("domain") })}</span>
        </label>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={() => setLinking(null)}>
            {t("Cancel")}
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
            {t("Link")}
          </Button>
        </div>
      </Dialog>

      <Dialog
        open={Boolean(removing)}
        title={t("Remove domain")}
        description={removing?.domain}
        onClose={() => setRemoving(null)}
      >
        <p className="text-sm text-muted-foreground">
          {(removing?.published ?? true)
            ? t("The proxy route is removed. The site and its primary domain are unchanged.")
            : t("The domain reservation is released. There is no published route or certificate.")}
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={() => setRemoving(null)}>
            {t("Cancel")}
          </Button>
          <Button
            size="sm"
            disabled={removeMutation.isPending}
            onClick={() => removing && removeMutation.mutate(removing.domain)}
          >
            {t("Remove")}
          </Button>
        </div>
      </Dialog>
    </section>
  );
}
