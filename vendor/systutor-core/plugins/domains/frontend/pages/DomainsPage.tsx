import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useMemo, useState } from "react";

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
import { Select } from "@systutor/shell/ui/select";
import { toast } from "@systutor/shell/ui/toast";
import { t } from "@/i18n";
import { DomainsTable } from "../components/DomainsTable";
import { CloudflareConnectButton } from "../cloudflare/CloudflareConnectButton";
import {
  cloudflareKeys,
  getCloudflareClient,
  getCloudflareStatus,
  listCloudflareZones,
  provisionApply,
  provisionPlan,
} from "../cloudflare/api";
import type {
  CloudflareProvisionApply,
  CloudflareProvisionPlan,
  CloudflareZone,
} from "../cloudflare/types";
import {
  canProvisionCloudflare,
  hasZoneWriteScope,
  isCloudflareConnected,
  needsClientRegistration,
} from "../cloudflare/logic";
import { DnsZoneDialog } from "../dns/DnsZoneDialog";
import { NameserversDialog } from "../dns/NameserversDialog";
import { dnsKeys, listZones } from "../dns/api";
import {
  activationZoneDomain,
  longestOwnedZone,
  normalizeDnsName,
} from "../dns/logic";

const RESERVAR = "";

function normalizeDomainInput(value: string): string {
  return value.trim().toLowerCase().replace(/\.$/, "");
}

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
  const cloudflareClientQuery = useQuery({
    queryKey: cloudflareKeys.client,
    queryFn: getCloudflareClient,
    enabled: canUseCloudflare,
    retry: false,
  });
  const cfConnected = isCloudflareConnected(cloudflareStatusQuery.data ?? null);
  const cloudflareZonesQuery = useQuery({
    queryKey: cloudflareKeys.zones,
    queryFn: listCloudflareZones,
    enabled: canUseCloudflare && cfConnected,
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

  // Cloudflare provisioning (Add domain)
  const [cfOpen, setCfOpen] = useState(false);
  const [cfIpv4, setCfIpv4] = useState("");
  const [cfReplace, setCfReplace] = useState(false);
  const [cfPlan, setCfPlan] = useState<CloudflareProvisionPlan | null>(null);
  const [cfApplyResult, setCfApplyResult] = useState<CloudflareProvisionApply | null>(null);
  const [cfError, setCfError] = useState<string | null>(null);
  const [cfDetailOpen, setCfDetailOpen] = useState(false);

  const items = domainsQuery.data?.items ?? [];
  const sites = domainsQuery.data?.sites ?? [];
  const ownSites = sites.filter(
    (site) => !site.tenant_id || site.tenant_id === currentTenant?.id
  );
  const dnsZones = zonesQuery.data?.items ?? [];
  const cloudflareZones = cloudflareZonesQuery.data?.items ?? [];
  const activeZone = dnsDomain ? longestOwnedZone(dnsDomain.domain, dnsZones) : null;
  const dnsSubdomains = activeZone ? items
    .filter((item) => !item.tenant_id || item.tenant_id === currentTenant?.id)
    .map((item) => normalizeDnsName(item.domain))
    .filter((name) => name !== normalizeDnsName(activeZone.name)
      && longestOwnedZone(name, dnsZones)?.name === activeZone.name)
    .sort() : [];

  const normalizedAddDomain = useMemo(() => normalizeDomainInput(domain), [domain]);
  const existingDomainItem = useMemo(
    () => items.find((item) => normalizeDnsName(item.domain) === normalizedAddDomain),
    [items, normalizedAddDomain]
  );
  const addDestinationSites = useMemo(
    () => ownSites.filter((site) => !existingDomainItem?.site_id || existingDomainItem.site_id !== site.site_id),
    [ownSites, existingDomainItem]
  );
  const linkDestinationSites = useMemo(
    () => ownSites.filter((site) => !linking?.site_id || linking.site_id !== site.site_id),
    [ownSites, linking]
  );
  const selectedSite = addDestinationSites.find((site) => site.site_id === siteId)
    ?? ownSites.find((site) => site.site_id === siteId);
  const linkTarget
    = linkDestinationSites.find((site) => site.site_id === linkSiteId) ?? linkDestinationSites[0];

  const cfEligible = canUseCloudflare && isCloudflareConnected(cloudflareStatusQuery.data ?? null);
  const cfProvisionable = canProvisionCloudflare(cloudflareStatusQuery.data ?? null)
    && !needsClientRegistration(cloudflareClientQuery.data ?? null);
  const cfNeedsZoneWrite = cfEligible && !hasZoneWriteScope(cloudflareStatusQuery.data ?? null);
  const cfNeedsRepair = needsClientRegistration(cloudflareClientQuery.data ?? null);

  function resetCloudflareState() {
    setCfOpen(false);
    setCfIpv4("");
    setCfReplace(false);
    setCfPlan(null);
    setCfApplyResult(null);
    setCfError(null);
    setCfDetailOpen(false);
  }

  const createMutation = useMutation({
    mutationFn: createDomain,
    onSuccess: () => {
      toast.success(t("Domain added"));
      void queryClient.invalidateQueries({ queryKey: domainsKeys.list });
      setAddOpen(false);
      setDomain("");
      setWww(false);
      resetCloudflareState();
    },
    onError: (error: Error) => setFormError(error.message),
  });

  const cfPlanMutation = useMutation({
    mutationFn: () => provisionPlan({
      domain: normalizedAddDomain,
      ipv4: cfIpv4.trim(),
      www,
      replace: cfReplace,
    }),
    onSuccess: (plan) => {
      setCfPlan(plan);
      setCfError(null);
    },
    onError: (error: Error) => {
      setCfPlan(null);
      setCfError(error.message);
    },
  });

  const cfApplyMutation = useMutation({
    mutationFn: () => provisionApply({
      domain: normalizedAddDomain,
      ipv4: cfIpv4.trim(),
      www,
      replace: cfReplace,
    }),
    onSuccess: (result) => {
      setCfApplyResult(result);
      setCfPlan(result);
      setCfError(null);
      toast.success(t("Cloudflare DNS applied"));
    },
    onError: (error: Error) => {
      setCfError(error.message);
    },
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
    resetCloudflareState();
    setAddOpen(true);
  }

  function openLink(item: DomainItem) {
    const candidates = ownSites.filter((site) => !item.site_id || item.site_id !== site.site_id);
    setLinkSiteId(candidates[0]?.site_id ?? "");
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

  const cfCanConfigure = cfProvisionable && normalizedAddDomain.length > 0;
  const cfPlanConflicts = cfPlan?.conflicts?.length ?? 0;
  const cfHasConflicts = cfPlanConflicts > 0;

  return (
    <section className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-foreground">{t("Domains and DNS")}</h1>
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

      <Dialog
        open={addOpen}
        title={t("Add domain")}
        onClose={() => { setAddOpen(false); setCfOpen(false); }}
        maxWidthClassName="max-w-3xl"
      >
        <form className="space-y-3" onSubmit={submit}>
          <label className="block space-y-1 text-sm text-foreground">
            <span>{t("Domain")}</span>
            <Input
              value={domain}
              onChange={(event) => {
                const next = event.target.value;
                setDomain(next);
                setCfPlan(null);
                setCfApplyResult(null);
                setCfError(null);
                const normalized = normalizeDomainInput(next);
                const existing = items.find((item) => normalizeDnsName(item.domain) === normalized);
                if (siteId && existing?.site_id === siteId) {
                  setSiteId(RESERVAR);
                }
              }}
              placeholder={t("example.com")}
              autoFocus
            />
          </label>
          <label className="block space-y-1 text-sm text-foreground">
            <span>{t("Destination")}</span>
            <Select
              value={siteId}
              onChange={setSiteId}
              options={[
                { value: RESERVAR, label: t("Reserve only (do not publish)") },
                ...addDestinationSites.map((site) => ({
                  value: site.site_id,
                  label: `${site.container}${site.primary_domain ? ` (${site.primary_domain})` : ""}`,
                })),
              ]}
            />
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
              onChange={(event) => {
                setWww(event.target.checked);
                setCfPlan(null);
                setCfApplyResult(null);
              }}
            />
            <span>{t("Also add www.{domain}", { domain: domain.trim() || t("domain") })}</span>
          </label>

          {canUseCloudflare ? (
            <div className="rounded-md border border-border/70 bg-muted/10 p-3 text-sm">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="space-y-1">
                  <p className="font-medium text-foreground/90">
                    {cfEligible ? t("Cloudflare active") : t("Cloudflare")}
                  </p>
                  {cfEligible && cfProvisionable ? (
                    <p className="text-xs text-muted-foreground">
                      {cfApplyResult
                        ? t("Cloudflare DNS applied for this domain.")
                        : cfPlan
                          ? t("Cloudflare plan ready. Apply it before adding the domain if needed.")
                          : t("DNS provisioning available (optional). Configure it in the nested dialog.")}
                    </p>
                  ) : cfEligible && (cfNeedsZoneWrite || cfNeedsRepair) ? (
                    <p className="text-xs text-muted-foreground">
                      {t("Cloudflare is connected but missing provisioning permission. Repair/reconnect to enable DNS provisioning.")}
                    </p>
                  ) : cfEligible ? (
                    <p className="text-xs text-muted-foreground">
                      {t("Cloudflare is connected. Provisioning availability depends on granted scopes.")}
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      {t("Cloudflare not active for this tenant. Fallback to local/PowerDNS management as applicable.")}
                    </p>
                  )}
                  {cfApplyResult ? (
                    <p className="text-xs text-emerald-500/90">
                      {t("Applied")}: {cfApplyResult.records?.length ?? cfApplyResult.desired_records?.length ?? 0} {t("records")} · DNS only
                    </p>
                  ) : null}
                </div>
                {cfEligible ? (
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    disabled={!cfCanConfigure || cfNeedsZoneWrite || cfNeedsRepair}
                    onClick={() => { setCfError(null); setCfOpen(true); }}
                  >
                    {t("Configure Cloudflare")}
                  </Button>
                ) : null}
              </div>
            </div>
          ) : null}

          {formError && <Alert title={t("Could not add")}>{formError}</Alert>}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => { setAddOpen(false); setCfOpen(false); }}
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
        open={cfOpen}
        title={t("Configure Cloudflare")}
        description={normalizedAddDomain ? `${normalizedAddDomain} · DNS only (proxied=false)` : undefined}
        onClose={() => setCfOpen(false)}
        maxWidthClassName="max-w-2xl"
        zIndexClassName="z-[1200]"
      >
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block space-y-1 text-sm text-foreground">
              <span>{t("Public origin IPv4")}</span>
              <Input
                value={cfIpv4}
                onChange={(event) => {
                  setCfIpv4(event.target.value);
                  setCfPlan(null);
                  setCfApplyResult(null);
                }}
                placeholder="203.0.113.10"
              />
            </label>
            <div className="flex items-end pb-2">
              <label className="flex items-center gap-2 text-sm text-foreground">
                <input type="checkbox" checked={www} onChange={(e) => { setWww(e.target.checked); setCfPlan(null); setCfApplyResult(null); }} />
                <span>{t("Include www")}</span>
              </label>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={!normalizedAddDomain || !cfIpv4.trim() || cfPlanMutation.isPending}
              onClick={() => cfPlanMutation.mutate()}
            >
              {cfPlanMutation.isPending ? t("Planning...") : t("View plan")}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={!cfPlan || cfApplyMutation.isPending || (cfHasConflicts && !cfReplace) || cfPlan.drift === false && cfApplyResult !== null}
              onClick={() => cfApplyMutation.mutate()}
            >
              {cfApplyMutation.isPending ? t("Applying...") : t("Apply Cloudflare DNS")}
            </Button>
          </div>

          {cfError ? <Alert title={t("Cloudflare provisioning error")}>{cfError}</Alert> : null}

          {cfPlan ? (
            <div className="rounded-md border border-border/70 bg-muted/10 p-3 text-sm space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-medium text-foreground/90">{t("Plan summary")}</p>
                <span className="text-xs text-muted-foreground">
                  {cfPlan.drift ? t("Drift detected") : t("Already converged")} · {t("Zone")}: {cfPlan.zone_action}
                </span>
              </div>
              <ul className="list-disc pl-5 text-xs text-muted-foreground space-y-0.5">
                <li>{t("Desired records")}: {cfPlan.desired_records?.length ?? 0} (DNS only, proxied=false)</li>
                <li>{t("Conflicts")}: {cfPlan.conflicts?.length ?? 0}</li>
                {cfPlan.nameservers?.length ? <li>{t("Nameservers")}: {cfPlan.nameservers.join(", ")}</li> : null}
              </ul>
              {cfPlan.zone_action === "create" ? (
                <Alert title={t("New zone")}>
                  {t("Zone will be created and may be pending. Delegate these nameservers at your registrar:")} {cfPlan.nameservers?.join(", ") || "—"}
                </Alert>
              ) : null}
              {cfHasConflicts ? (
                <div className="space-y-2">
                  <Alert title={t("Conflicts detected")}>
                    {t("Existing A/AAAA/CNAME records conflict at managed owners. Enable replace to remove only conflicting records and apply desired ones.")}
                  </Alert>
                  <label className="flex items-center gap-2 text-xs text-foreground">
                    <input type="checkbox" checked={cfReplace} onChange={(e) => { setCfReplace(e.target.checked); setCfPlan(null); }} />
                    <span>{t("Replace conflicting records (explicit)")}</span>
                  </label>
                </div>
              ) : null}
              <button type="button" className="text-xs text-primary underline-offset-2 hover:underline" onClick={() => setCfDetailOpen((v) => !v)}>
                {cfDetailOpen ? t("Hide details") : t("View details")}
              </button>
              {cfDetailOpen ? (
                <div className="space-y-2 text-xs">
                  <div>
                    <p className="font-medium text-foreground/80">{t("Desired records")}</p>
                    <ul className="mt-1 space-y-1 font-mono">
                      {(cfPlan.desired_records ?? []).map((r, i) => (
                        <li key={i}>{r.type} {r.name} → {r.content} (ttl {r.ttl}, proxied {String(r.proxied)})</li>
                      ))}
                    </ul>
                  </div>
                  {cfPlan.conflicts?.length ? (
                    <div>
                      <p className="font-medium text-foreground/80">{t("Conflicts")}</p>
                      <ul className="mt-1 space-y-1 font-mono">
                        {cfPlan.conflicts.map((r, i) => (
                          <li key={i}>{r.type} {r.name} → {r.content}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </div>
              ) : null}
              {cfApplyResult ? (
                <p className="text-xs text-emerald-500/90">{t("Applied successfully. You can now add the domain in Spanel.")}</p>
              ) : null}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              {t("Enter the public origin IPv4 and view the plan before applying. No public DNS changes are made until you apply.")}
            </p>
          )}
        </div>
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
          <Select
            value={linkTarget?.site_id ?? ""}
            onChange={setLinkSiteId}
            options={linkDestinationSites.map((site) => ({
              value: site.site_id,
              label: `${site.container}${site.primary_domain ? ` (${site.primary_domain})` : ""}`,
            }))}
          />
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
        cloudflareZone={cloudflareZone}
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
        onClose={() => {
          setDnsDomain(null);
          setCloudflareZone(null);
        }}
      />
      <NameserversDialog open={nameserversOpen} onClose={() => setNameserversOpen(false)}
        permissions={permissions} zones={zonesQuery.data?.items ?? []} />
    </section>
  );
}
