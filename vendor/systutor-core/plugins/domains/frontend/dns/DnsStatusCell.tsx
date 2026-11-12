import { t } from "@/i18n";
import type { DnsStatus } from "./types";

const styles: Record<DnsStatus, { icon: string; label: string; tone: string }> = {
  managed: { icon: "●", label: "Managed", tone: "text-emerald-600" },
  external: { icon: "○", label: "External", tone: "text-muted-foreground" },
  "no-zone": { icon: "◌", label: "No zone", tone: "text-amber-600" },
  error: { icon: "⚠", label: "Error", tone: "text-amber-600" },
};

export function DnsStatusCell({ status }: { status: DnsStatus }) {
  const value = styles[status];
  return <span className={`whitespace-nowrap text-xs font-medium ${value.tone}`}>
    <span aria-hidden>{value.icon}</span> {t(value.label)}
  </span>;
}
