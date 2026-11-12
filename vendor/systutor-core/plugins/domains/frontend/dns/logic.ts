import type {
  DnsStatus,
  NameserverPlan,
  NameserverSettings,
  RrsetDraft,
  ZoneItem,
} from "./types";

export const SUPPORTED_TYPES = ["A", "AAAA", "CNAME", "MX", "TXT", "CAA", "SRV", "NS"] as const;
const INTERACTIVE = new Set(["BUTTON", "A", "INPUT", "SELECT", "TEXTAREA"]);

export function normalizeDnsName(value: string) {
  return value.trim().toLowerCase().replace(/\.$/, "");
}

export function longestOwnedZone(domain: string, zones: ZoneItem[]) {
  const candidate = normalizeDnsName(domain);
  return zones
    .filter(({ name }) => candidate === normalizeDnsName(name)
      || candidate.endsWith(`.${normalizeDnsName(name)}`))
    .sort((left, right) => right.name.length - left.name.length)[0] ?? null;
}

export function activationZoneDomain(domain: string, registeredDomains: string[]) {
  const candidate = normalizeDnsName(domain);
  return registeredDomains
    .map(normalizeDnsName)
    .filter((name) => candidate.endsWith(`.${name}`))
    .sort((left, right) => right.length - left.length)[0] ?? candidate;
}

export type DomainGroup<T> = { parent: T; children: T[] };

export function groupRegisteredDomains<
  T extends { domain: string; tenant_id?: string | null },
>(items: T[]): DomainGroup<T>[] {
  const sameTenant = (left: T, right: T) => (left.tenant_id ?? null) === (right.tenant_id ?? null);
  const roots = items.filter((item) => !items.some((candidate) => sameTenant(item, candidate)
    && normalizeDnsName(item.domain).endsWith(`.${normalizeDnsName(candidate.domain)}`)));
  return roots.map((parent) => ({
    parent,
    children: items.filter((item) => sameTenant(item, parent)
      && normalizeDnsName(item.domain).endsWith(`.${normalizeDnsName(parent.domain)}`)),
  }));
}

export function dnsNameInScope(name: string, scope: string) {
  const candidate = normalizeDnsName(name);
  const boundary = normalizeDnsName(scope);
  return !boundary || candidate === boundary || candidate.endsWith(`.${boundary}`);
}

export function dnsStatus(domain: string, zones: ZoneItem[], failed = false): DnsStatus {
  if (failed) return "error";
  if (longestOwnedZone(domain, zones)) return "managed";
  return zones.length ? "external" : "no-zone";
}

export function shouldOpenDnsRow(owned: boolean, targetTag: string, key?: string) {
  return owned && !INTERACTIVE.has(targetTag.toUpperCase()) && (key === undefined || key === "Enter");
}

export function canConfigureNameservers(permissions: string[]) {
  return permissions.includes("domains.dns.nameservers")
    && permissions.includes("core.platform.admin");
}

export function isLockedRrset(zone: string, name: string, type: string) {
  const owner = normalizeDnsName(name);
  return (owner === "@" || owner === normalizeDnsName(zone)) && (type === "SOA" || type === "NS");
}

export function relativeOwner(zone: string, name: string) {
  const zoneName = normalizeDnsName(zone);
  const owner = normalizeDnsName(name);
  if (owner === zoneName || owner === "@") return "@";
  const suffix = `.${zoneName}`;
  return owner.endsWith(suffix) ? owner.slice(0, -suffix.length) : owner;
}

export function describePlanChanges(changes: Array<Record<string, unknown>>) {
  return changes.map((change) => {
    const action = String(change.action ?? "change");
    const marker = action === "add" ? "+" : action === "delete" ? "-" : "~";
    const records = Array.isArray(change.records)
      ? change.records.map(String).sort().join(" | ")
      : "";
    return [marker, change.name, change.type, change.ttl, records]
      .filter((value) => value !== undefined && value !== "")
      .join(" ");
  }).sort();
}

export function validateRrset(draft: RrsetDraft) {
  const errors: Record<string, string> = {};
  if (!draft.name.trim()) errors.name = "Name is required.";
  if (!(SUPPORTED_TYPES as readonly string[]).includes(draft.type)) errors.type = "Unsupported record type.";
  if (!Number.isInteger(draft.ttl) || draft.ttl < 1 || draft.ttl > 604800) errors.ttl = "TTL must be between 1 and 604800.";
  const values = draft.values.map((value) => value.trim()).filter(Boolean);
  if (!values.length) errors.values = "At least one value is required.";
  if (draft.type === "CNAME" && values.length !== 1) errors.values = "CNAME requires exactly one value.";
  if (draft.type === "MX" && values.some((value) => !/^\d+\s+\S+$/.test(value))) errors.values = "MX values use: priority host.";
  if (draft.type === "SRV" && values.some((value) => !/^\d+\s+\d+\s+\d+\s+\S+$/.test(value))) errors.values = "SRV values use: priority weight port target.";
  return errors;
}

export function filterRrsets<T extends { name: string; type: string; records: string[] }>(
  rrsets: T[], search: string, type: string,
) {
  const needle = search.trim().toLowerCase();
  return rrsets.filter((rrset) => (!type || rrset.type === type)
    && (!needle || [rrset.name, rrset.type, ...rrset.records]
      .some((value) => value.toLowerCase().includes(needle))));
}

export function buildNameserverApply(
  settings: NameserverSettings,
  plan: NameserverPlan,
  approved: string[],
) {
  return {
    settings,
    zones: approved,
    zone_versions: Object.fromEntries(approved.map((zone) => [zone, plan.zone_versions[zone]])),
    expected_version: plan.settings_version,
  };
}

export function canApplyNameserverPlan(plan: NameserverPlan, approved: string[], hasZones: boolean) {
  return approved.length > 0 || !hasZones;
}

export function safeErrorMessage(error: unknown) {
  const message = error instanceof Error ? error.message : "DNS request failed.";
  return /api[-_ ]?key|authorization|bearer|TXT\s+[^ ]+/i.test(message)
    ? "DNS request failed."
    : message;
}
