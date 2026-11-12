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
import { DnsStatusCell } from "../dns/DnsStatusCell";
import { DnsZoneDialog } from "../dns/DnsZoneDialog";
import { NameserversDialog } from "../dns/NameserversDialog";
import { dnsKeys, listZones } from "../dns/api";
import { dnsStatus, longestOwnedZone, shouldOpenDnsRow } from "../dns/logic";

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
      <span className="text-xs text-muted-foreground" title={t("Reserved domain, not published")}>
        {readable ? t("Not published") : t("Read only")}
      </span>
    );
  }

  const info = query.data;
  const tooltip = info
    ? [info.issuer, info.not_after, info.error].filter(Boolean).join(" · ")
    : t("Not verified yet");

  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className={`truncate text-xs ${certificateTone(info)}`} title={tooltip}>
        {certificateLabel(info)}
      </span>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        className="h-7 shrink-0 px-2 text-xs"
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
  const canReadDns = permissions.includes("domains.dns.read");

  const domainsQuery = useQuery({
    queryKey: [...domainsKeys.list, platform ? "all" : "tenant"],
    queryFn: () => listDomains(platform),
  });
  const zonesQuery = useQuery({
    queryKey: dnsKeys.zones,
    queryFn: listZones,
    enabled: canReadDns,
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
  const [dnsDomain, setDnsDomain] = useState<DomainItem | null>(null);
  const [nameserversOpen, setNameserversOpen] = useState(false);

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
          {canReadDns && <p className="text-sm text-muted-foreground">
            {t("Double-click a domain to access its DNS zone.")}
          </p>}
        </div>
        <div className="flex gap-2">
          {canReadDns && <Button size="sm" variant="secondary" onClick={() => setNameserversOpen(true)}>{t("Nameservers")}</Button>}
          {canManage && <Button size="sm" onClick={openAdd}>{t("Add domain")}</Button>}
        </div>
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
        <div className="overflow-x-auto rounded-lg border border-border bg-card">
          <table className="w-full min-w-[760px] table-fixed border-collapse text-sm">
            <colgroup>
              <col className="w-[29%]" />
              {platform && <col className="w-[15%]" />}
              <col className="w-[10%]" />
              <col className="w-[12%]" />
              <col className="w-[18%]" />
              {canReadDns && <col className="w-[12%]" />}
              <col className="w-[16%]" />
            </colgroup>
            <thead className="bg-muted/70 text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-3 py-2.5 font-medium">{t("Domain")}</th>
                {platform && <th className="px-3 py-2.5 font-medium">{t("Tenant")}</th>}
                <th className="px-3 py-2.5 font-medium">{t("Status")}</th>
                <th className="px-3 py-2.5 font-medium">{t("Owner")}</th>
                <th className="px-3 py-2.5 font-medium">{t("Certificate")}</th>
                {canReadDns && <th className="px-3 py-2.5 font-medium">{t("DNS")}</th>}
                <th className="px-3 py-2.5 text-right font-medium">{t("Actions")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const extraConflict = item.conflict;
                const published = item.published ?? true;
                const isOwned = !item.tenant_id || item.tenant_id === currentTenant?.id;
                return (
                  <tr
                    key={item.domain}
                    className={`border-t border-border/70 transition-colors hover:bg-muted/30 ${canReadDns && isOwned ? "cursor-pointer focus:outline-none focus:ring-2 focus:ring-primary" : ""}`}
                    tabIndex={canReadDns && isOwned ? 0 : undefined}
                    aria-label={canReadDns && isOwned ? t("Open DNS zone for {domain}", { domain: item.domain }) : undefined}
                    onDoubleClick={(event) => {
                      const blocked = (event.target as HTMLElement).closest("button,a,input,select,textarea");
                      if (canReadDns && shouldOpenDnsRow(isOwned, blocked?.tagName ?? (event.target as HTMLElement).tagName)) setDnsDomain(item);
                    }}
                    onKeyDown={(event) => {
                      if (event.currentTarget !== event.target && (event.target as HTMLElement).closest("button,a,input,select,textarea")) return;
                      if (canReadDns && shouldOpenDnsRow(isOwned, (event.target as HTMLElement).tagName, event.key)) setDnsDomain(item);
                    }}
                  >
                    <td className="px-3 py-3 font-medium text-foreground">
                      <div className="flex min-w-0 items-center gap-2">
                        <span className="truncate" title={item.domain}>
                          {item.domain}
                        </span>
                        {extraConflict && (
                          <span
                            className="shrink-0 rounded bg-red-100 px-1.5 py-0.5 text-xs
                              font-medium text-red-700"
                          >
                            {t("Two owners")}
                          </span>
                        )}
                      </div>
                    </td>
                    {platform && (
                      <td className="px-3 py-3 text-muted-foreground">
                        <div
                          className="truncate"
                          title={item.tenant_name ?? item.tenant_id ?? undefined}
                        >
                          {item.tenant_name ?? item.tenant_id ?? "—"}
                        </div>
                      </td>
                    )}
                    <td className="px-3 py-3">
                      <span
                        className={`inline-flex items-center gap-1.5 whitespace-nowrap
                          text-xs font-medium ${
                            published ? "text-emerald-600" : "text-amber-600"
                          }`}
                      >
                        <span
                          className={`h-1.5 w-1.5 rounded-full ${
                            published ? "bg-emerald-500" : "bg-amber-500"
                          }`}
                        />
                        {published ? t("Published") : t("Not published")}
                      </span>
                    </td>
                    {canReadDns && <td className="px-3 py-3">
                      <DnsStatusCell status={dnsStatus(item.domain, zonesQuery.data?.items ?? [], zonesQuery.isError)} />
                    </td>}
                    <td className="px-3 py-3">
                      {item.owner === "hosting" ? (
                        <span
                          className="inline-flex items-center gap-1 whitespace-nowrap
                            rounded-full border border-border bg-muted px-2 py-0.5 text-xs
                            text-muted-foreground"
                          title={t("Managed by Hosting")}
                        >
                          {t("Hosting")} <span aria-hidden>🔒</span>
                        </span>
                      ) : (
                        <span className="text-xs text-foreground">
                          {published ? t("Extra") : t("Domain")}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-3">
                      <CertificateCell
                        domain={item.domain}
                        published={published}
                        readable={isOwned}
                      />
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex items-center justify-end gap-1.5">
                        {item.owner === "domains" && canManage && isOwned && item.editable && (
                          <>
                            {!published && ownSites.length > 0 && (
                              <Button
                                variant="secondary"
                                className="h-7 whitespace-nowrap px-2 text-xs"
                                onClick={() => openLink(item)}
                              >
                                {t("Link")}
                              </Button>
                            )}
                            <Button
                              variant="secondary"
                              className="h-7 whitespace-nowrap px-2 text-xs"
                              onClick={() => setRemoving(item)}
                            >
                              {t("Remove")}
                            </Button>
                          </>
                        )}
                        {extraConflict && canManage && isOwned && item.owner !== "domains" && (
                          <Button
                            variant="secondary"
                            className="h-7 whitespace-nowrap px-2 text-xs"
                            onClick={() => setRemoving(item)}
                          >
                            {t("Remove extra")}
                          </Button>
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

      <DnsZoneDialog
        domain={dnsDomain?.domain ?? null}
        zoneName={dnsDomain ? longestOwnedZone(dnsDomain.domain, zonesQuery.data?.items ?? [])?.name ?? null : null}
        permissions={permissions}
        owned={Boolean(dnsDomain && (!dnsDomain.tenant_id || dnsDomain.tenant_id === currentTenant?.id))}
        zoneListError={zonesQuery.isError}
        retryZoneList={() => void zonesQuery.refetch()}
        onClose={() => setDnsDomain(null)}
      />
      <NameserversDialog open={nameserversOpen} onClose={() => setNameserversOpen(false)}
        permissions={permissions} zones={zonesQuery.data?.items ?? []} />
    </section>
  );
}
