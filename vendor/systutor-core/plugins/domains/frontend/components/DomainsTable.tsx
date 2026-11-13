import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { t } from "@/i18n";
import { Button } from "@systutor/shell/ui/button";
import { Pagination } from "@systutor/shell/ui/pagination";

import {
  domainsKeys,
  getCertificate,
  type CertificateInfo,
  type DomainItem,
} from "../api";
import { DnsStatusCell } from "../dns/DnsStatusCell";
import {
  dnsStatus,
  groupRegisteredDomains,
  longestOwnedZone,
  shouldOpenDnsRow,
} from "../dns/logic";
import type { ZoneItem } from "../dns/types";
import type { CloudflareZone } from "../cloudflare/types";

function certificateLabel(info: CertificateInfo | undefined) {
  if (!info) return t("No data");
  if (info.state === "active" && info.verified) return t("Valid");
  if (info.state === "pending_dns") return t("No DNS");
  return t("Pending");
}

function CertificateCell({ domain, published, readable }: {
  domain: string;
  published: boolean;
  readable: boolean;
}) {
  const query = useQuery({
    queryKey: domainsKeys.certificate(domain),
    queryFn: () => getCertificate(domain),
    enabled: false,
    retry: false,
    staleTime: 60_000,
  });
  if (!published || !readable) return <span className="text-xs text-muted-foreground"
    title={t("Reserved domain, not published")}>
    {readable ? t("Not published") : t("Read only")}
  </span>;
  const info = query.data;
  const tone = !info ? "text-muted-foreground"
    : info.state === "active" && info.verified ? "text-emerald-600" : "text-amber-600";
  const tooltip = info
    ? [info.issuer, info.not_after, info.error].filter(Boolean).join(" · ")
    : t("Not verified yet");
  return <div className="flex min-w-0 items-center gap-2">
    <span className={`truncate text-xs ${tone}`} title={tooltip}>{certificateLabel(info)}</span>
    <Button type="button" variant="secondary" size="sm" className="h-7 shrink-0 px-2 text-xs"
      onClick={() => void query.refetch()} disabled={query.isFetching}>
      {query.isFetching ? "..." : t("Verify")}
    </Button>
  </div>;
}

export function DomainsTable({ items, zones, cloudflareZones, dnsError, platform, canReadDns, canManage,
  currentTenantId, hasOwnSites, onOpenDns, onLink, onRemove }: {
  items: DomainItem[];
  zones: ZoneItem[];
  cloudflareZones: CloudflareZone[];
  dnsError: boolean;
  platform: boolean;
  canReadDns: boolean;
  canManage: boolean;
  currentTenantId: string | undefined;
  hasOwnSites: boolean;
  onOpenDns: (item: DomainItem, cloudflareZone: CloudflareZone | null) => void;
  onLink: (item: DomainItem) => void;
  onRemove: (item: DomainItem) => void;
}) {
  const [expandedDomains, setExpandedDomains] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(1);
  const DOMAINS_PAGE_SIZE = 10;
  const groups = groupRegisteredDomains(items);
  const totalPages = Math.max(1, Math.ceil(groups.length / DOMAINS_PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  const pagedGroups = groups.slice(
    (currentPage - 1) * DOMAINS_PAGE_SIZE,
    currentPage * DOMAINS_PAGE_SIZE,
  );
  const rows = pagedGroups.flatMap(({ parent, children }) => [
    { item: parent, child: false, childCount: children.length },
    ...(expandedDomains.has(`${parent.tenant_id ?? ""}:${parent.domain}`)
      ? children.map((item) => ({ item, child: true, childCount: 0 })) : []),
  ]);

  return <div className="space-y-3">
    <div className="overflow-x-auto rounded-lg border border-border bg-card">
    <table className="w-full min-w-[760px] table-fixed border-collapse text-sm">
      <colgroup><col className="w-[29%]" />{platform && <col className="w-[15%]" />}
        <col className="w-[10%]" /><col className="w-[12%]" /><col className="w-[18%]" />
        {canReadDns && <col className="w-[12%]" />}<col className="w-[16%]" /></colgroup>
      <thead className="bg-muted/70 text-left text-xs uppercase tracking-wide text-muted-foreground">
        <tr><th className="px-3 py-2.5 font-medium">{t("Domain")}</th>
          {platform && <th className="px-3 py-2.5 font-medium">{t("Tenant")}</th>}
          {[
            "Status", "Owner", "Certificate", ...(canReadDns ? ["DNS"] : []), "Actions",
          ].map((label) => <th className={`px-3 py-2.5 font-medium ${label === "Actions" ? "text-right" : ""}`} key={label}>{t(label)}</th>)}</tr>
      </thead>
      <tbody>{rows.map(({ item, child, childCount }) => {
        const published = item.published ?? true;
        const owned = !item.tenant_id || item.tenant_id === currentTenantId;
        const managedZone = longestOwnedZone(item.domain, zones);
        const cloudflareZone = longestOwnedZone(item.domain, cloudflareZones);
        const key = `${item.tenant_id ?? ""}:${item.domain}`;
        const expanded = expandedDomains.has(key);
        return <tr key={key}
          className={`border-t border-border/70 transition-colors hover:bg-muted/30 ${child ? "bg-muted/20" : ""} ${canReadDns && owned ? "cursor-pointer focus:outline-none focus:ring-2 focus:ring-primary" : ""}`}
          tabIndex={canReadDns && owned ? 0 : undefined}
          aria-label={canReadDns && owned ? t("Open DNS zone for {domain}", { domain: item.domain }) : undefined}
          onDoubleClick={(event) => {
            const blocked = (event.target as HTMLElement).closest("button,a,input,select,textarea");
            if (canReadDns && shouldOpenDnsRow(owned, blocked?.tagName
              ?? (event.target as HTMLElement).tagName)) {
              onOpenDns(item, managedZone ? null : cloudflareZone);
            }
          }}
          onKeyDown={(event) => {
            if (event.currentTarget !== event.target
              && (event.target as HTMLElement).closest("button,a,input,select,textarea")) return;
            if (canReadDns && shouldOpenDnsRow(
              owned, (event.target as HTMLElement).tagName, event.key,
            )) onOpenDns(item, managedZone ? null : cloudflareZone);
          }}>
          <td className="px-3 py-3 font-medium text-foreground">
            <div className={`flex min-w-0 gap-2 ${child ? "pl-6" : ""}`}>
              {!child && childCount > 0 ? <button type="button"
                className="mt-0.5 h-5 w-5 shrink-0 rounded text-muted-foreground hover:bg-muted"
                aria-expanded={expanded}
                aria-label={t(expanded ? "Collapse subdomains of {domain}"
                  : "Expand subdomains of {domain}", { domain: item.domain })}
                onClick={() => setExpandedDomains((current) => {
                  const next = new Set(current);
                  if (next.has(key)) next.delete(key); else next.add(key);
                  return next;
                })}><span aria-hidden>{expanded ? "▾" : "▸"}</span></button>
                : child ? <span className="shrink-0 text-muted-foreground" aria-hidden>↳</span>
                  : <span className="w-5 shrink-0" />}
              <div className="min-w-0"><div className="flex min-w-0 items-center gap-2">
                <span className="truncate" title={item.domain}>{item.domain}</span>
                {item.conflict && <span className="shrink-0 rounded bg-red-100 px-1.5 py-0.5 text-xs font-medium text-red-700">{t("Two owners")}</span>}
              </div>{!child && childCount > 0 && <div className="text-xs font-normal text-muted-foreground">
                {childCount === 1 ? t("1 subdomain") : t("{count} subdomains", { count: childCount })}
              </div>}</div>
            </div>
          </td>
          {platform && <td className="px-3 py-3 text-muted-foreground"><div className="truncate"
            title={item.tenant_name ?? item.tenant_id ?? undefined}>
            {item.tenant_name ?? item.tenant_id ?? "—"}</div></td>}
          <td className="px-3 py-3"><span className={`inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium ${published ? "text-emerald-600" : "text-amber-600"}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${published ? "bg-emerald-500" : "bg-amber-500"}`} />
            {published ? t("Published") : t("Not published")}</span></td>
          <td className="px-3 py-3">{item.owner === "hosting"
            ? <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground" title={t("Managed by Hosting")}>{t("Hosting")} <span aria-hidden>🔒</span></span>
            : <span className="text-xs text-foreground">{published ? t("Extra") : t("Domain")}</span>}</td>
          <td className="px-3 py-3"><CertificateCell domain={item.domain}
            published={published} readable={owned} /></td>
          {canReadDns && <td className="px-3 py-3"><DnsStatusCell
            status={dnsStatus(item.domain, zones, dnsError, cloudflareZones)} domain={item.domain}
            zoneName={managedZone?.name ?? cloudflareZone?.name ?? null} /></td>}
          <td className="px-3 py-3"><div className="flex items-center justify-end gap-1.5">
            {item.owner === "domains" && canManage && owned && item.editable && <>
              {!published && hasOwnSites && <Button variant="secondary" className="h-7 whitespace-nowrap px-2 text-xs" onClick={() => onLink(item)}>{t("Link")}</Button>}
              <Button variant="secondary" className="h-7 whitespace-nowrap px-2 text-xs" onClick={() => onRemove(item)}>{t("Remove")}</Button>
            </>}
            {item.conflict && canManage && owned && item.owner !== "domains" && <Button
              variant="secondary" className="h-7 whitespace-nowrap px-2 text-xs"
              onClick={() => onRemove(item)}>{t("Remove extra")}</Button>}
          </div></td>
        </tr>;
      })}</tbody>
    </table>
    </div>
    <Pagination page={currentPage} totalPages={totalPages} onChange={setPage} />
  </div>;
}
