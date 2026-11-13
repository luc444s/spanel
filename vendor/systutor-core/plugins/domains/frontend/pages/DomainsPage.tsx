import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";

import { useAuthStore } from "../../../../../../apps/web/src/features/auth/store";
import {
  createDomain,
  domainsKeys,
  linkDomain,
  listDomains,
  removeDomain,
  type DomainItem,
} from "../api";
import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { toast } from "@systutor/shell/ui/toast";
import { t } from "@/i18n";
import { DomainsTable } from "../components/DomainsTable";
import { CloudflareConnectButton } from "../cloudflare/CloudflareConnectButton";
import { CloudflareDnsDialog } from "../cloudflare/CloudflareDnsDialog";
import {
  cloudflareKeys,
  getCloudflareStatus,
  listCloudflareZones,
} from "../cloudflare/api";
import type { CloudflareZone } from "../cloudflare/types";
import { DnsZoneDialog } from "../dns/DnsZoneDialog";
import { NameserversDialog } from "../dns/NameserversDialog";
import { dnsKeys, listZones } from "../dns/api";
import {
  activationZoneDomain,
  longestOwnedZone,
  normalizeDnsName,
} from "../dns/logic";

const RESERVAR = "";

export default function DomainsPage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((state) => state.permissions);
  const currentTenant = useAuthStore((state) => state.currentTenant);
  const platform = permissions.includes("core.platform.admin");
  const canManage = permissions.includes("domains.domains.manage");
  const canReadDns = permissions.includes("domains.dns.read");
  const canUseCloudflare = permissions.includes("domains.dns.cloudflare");

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
  const cloudflareStatusQuery = useQuery({
    queryKey: cloudflareKeys.status,
    queryFn: getCloudflareStatus,
    enabled: canUseCloudflare,
    retry: false,
  });
  const cloudflareConnected = Boolean(
    cloudflareStatusQuery.data?.connected
      && cloudflareStatusQuery.data.status === "active",
  );
  const cloudflareZonesQuery = useQuery({
    queryKey: cloudflareKeys.zones,
    queryFn: listCloudflareZones,
    enabled: canUseCloudflare && cloudflareConnected,
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
  const [cloudflareZone, setCloudflareZone] = useState<CloudflareZone | null>(null);
  const [nameserversOpen, setNameserversOpen] = useState(false);

  const items = domainsQuery.data?.items ?? [];
  const sites = domainsQuery.data?.sites ?? [];
  const ownSites = sites.filter(
    (site) => !site.tenant_id || site.tenant_id === currentTenant?.id
  );
  const selectedSite = ownSites.find((site) => site.site_id === siteId);
  const linkTarget
    = ownSites.find((site) => site.site_id === linkSiteId) ?? ownSites[0];
  const dnsZones = zonesQuery.data?.items ?? [];
  const cloudflareZones = cloudflareZonesQuery.data?.items ?? [];
  const activeZone = dnsDomain ? longestOwnedZone(dnsDomain.domain, dnsZones) : null;
  const dnsSubdomains = activeZone ? items
    .filter((item) => !item.tenant_id || item.tenant_id === currentTenant?.id)
    .map((item) => normalizeDnsName(item.domain))
    .filter((name) => name !== normalizeDnsName(activeZone.name)
      && longestOwnedZone(name, dnsZones)?.name === activeZone.name)
    .sort() : [];

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

  function openDns(item: DomainItem, matchedCloudflareZone: CloudflareZone | null) {
    if (matchedCloudflareZone) {
      setDnsDomain(null);
      setCloudflareZone(matchedCloudflareZone);
      return;
    }
    setCloudflareZone(null);
    setDnsDomain(item);
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
          <CloudflareConnectButton
            permissions={permissions}
            onConnected={() => void cloudflareZonesQuery.refetch()}
          />
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
        <DomainsTable items={items} zones={dnsZones} cloudflareZones={cloudflareZones}
          dnsError={zonesQuery.isError}
          platform={platform} canReadDns={canReadDns} canManage={canManage}
          currentTenantId={currentTenant?.id} hasOwnSites={ownSites.length > 0}
          onOpenDns={openDns} onLink={openLink} onRemove={setRemoving} />
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
        activationZoneName={dnsDomain
          ? activationZoneDomain(
            dnsDomain.domain,
            items
              .filter((item) => !item.tenant_id || item.tenant_id === currentTenant?.id)
              .map((item) => item.domain),
          )
          : null}
        subdomains={dnsSubdomains}
        permissions={permissions}
        owned={Boolean(dnsDomain && (!dnsDomain.tenant_id || dnsDomain.tenant_id === currentTenant?.id))}
        zoneListError={zonesQuery.isError}
        retryZoneList={() => void zonesQuery.refetch()}
        onClose={() => setDnsDomain(null)}
      />
      <CloudflareDnsDialog zone={cloudflareZone} onClose={() => setCloudflareZone(null)} />
      <NameserversDialog open={nameserversOpen} onClose={() => setNameserversOpen(false)}
        permissions={permissions} zones={zonesQuery.data?.items ?? []} />
    </section>
  );
}
