import { t } from "@/i18n";
import { normalizeDnsName } from "./logic";
import type { DnsStatus } from "./types";

const styles: Record<DnsStatus, { icon: string; label: string; tone: string }> = {
  managed: { icon: "●", label: "Managed", tone: "text-emerald-600" },
  cloudflare: { icon: "☁", label: "Cloudflare", tone: "text-orange-600" },
  external: { icon: "○", label: "External", tone: "text-muted-foreground" },
  "no-zone": { icon: "◌", label: "No zone", tone: "text-amber-600" },
  error: { icon: "⚠", label: "Error", tone: "text-amber-600" },
};

export function DnsStatusCell({ status, domain, zoneName }: {
  status: DnsStatus;
  domain: string;
  zoneName: string | null;
}) {
  const value = styles[status];
  const label = status === "managed" && zoneName
    && normalizeDnsName(domain) !== normalizeDnsName(zoneName)
    ? t("Managed by {zone}", { zone: zoneName })
    : t(value.label);
  return <span className={`whitespace-nowrap text-xs font-medium ${value.tone}`}
    title={status === "cloudflare" && zoneName ? zoneName : undefined}>
    <span aria-hidden>{value.icon}</span> {label}
  </span>;
}
