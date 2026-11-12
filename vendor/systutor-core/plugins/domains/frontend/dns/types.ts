export type ZoneItem = {
  name: string;
  kind: string;
  version: string | null;
};

export type ZoneList = { items: ZoneItem[] };

export type Rrset = {
  name: string;
  type: string;
  ttl: number;
  records: string[];
};

export type RrsetDraft = {
  name: string;
  type: string;
  ttl: number;
  values: string[];
};

export type Zone = ZoneItem & {
  serial?: number | null;
  rrsets: Rrset[];
};

export type Plan = {
  zone: string;
  version: string;
  drift: boolean;
  changes: Array<Record<string, unknown>>;
};

export type NameserverSettings = {
  ns1: string;
  ns2: string;
  ns1_ipv4: string | null;
  ns1_ipv6: string | null;
  ns2_ipv4: string | null;
  ns2_ipv6: string | null;
  soa_contact: string;
  default_ttl: number;
};

export type NameserverState = Partial<NameserverSettings> & {
  configured: boolean;
  version: number;
  physical_redundancy: boolean;
  warning: string;
};

export type NameserverPlanZone = {
  zone?: string;
  name?: string;
  version?: string;
  drift?: boolean;
  changes?: unknown[];
};

export type NameserverPlan = {
  settings_version: number;
  settings_changed: boolean;
  settings_fields: string[];
  zones: NameserverPlanZone[];
  zone_versions: Record<string, string>;
  drift: boolean;
  warning: string;
};

export type VerifyResult = {
  zone: string;
  resolver: string;
  port: number;
  transports: Record<string, Record<string, unknown>>;
  warning: string;
};

export type DnsStatus = "managed" | "external" | "no-zone" | "error";
