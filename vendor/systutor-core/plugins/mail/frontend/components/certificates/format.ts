import { t } from "@/i18n";

import type { MailCertificate } from "../../api";

export function statusLabel(status: MailCertificate["status"]) {
  if (status === "active") return t("Valid");
  if (status === "expiring") return t("Expiring soon");
  if (status === "expired") return t("Expired");
  if (status === "missing") return t("No certificate");
  return t("Error");
}

export function statusTone(status: MailCertificate["status"]) {
  if (status === "active") return "text-emerald-600";
  if (status === "expiring") return "text-amber-600";
  if (status === "expired" || status === "error") return "text-destructive";
  return "text-muted-foreground";
}

export function sourceLabel(cert: MailCertificate) {
  if (cert.scope === "global") return t("Global · Default");
  const source =
    cert.source === "certbot"
      ? t("Certbot")
      : cert.source === "step-ca"
        ? t("step-ca")
        : t("Static");
  if (cert.deployment === "sni") return t("{source} · SNI", { source });
  if (cert.deployment === "none") return t("{source} · Not deployed", { source });
  if (cert.deployment === "failed") return t("{source} · Failed", { source });
  return t("{source} · Default", { source });
}

export function deploymentLabel(cert: MailCertificate) {
  if (cert.deployment === "sni") return t("SNI");
  if (cert.deployment === "default") return t("Default");
  if (cert.deployment === "none") return t("Not deployed");
  return t("Failed");
}

export function expiryLabel(cert: MailCertificate) {
  if (!cert.not_after) return t("No date");
  const target = new Date(cert.not_after).getTime();
  if (Number.isNaN(target)) return t("No date");
  const days = Math.ceil((target - Date.now()) / 86_400_000);
  if (days < 0) {
    return Math.abs(days) === 1
      ? t("Expired {count} day ago", { count: Math.abs(days) })
      : t("Expired {count} days ago", { count: Math.abs(days) });
  }
  if (days === 0) return t("Expires today");
  return days === 1
    ? t("Expires in {count} day", { count: days })
    : t("Expires in {count} days", { count: days });
}

export function timestampLabel(value: string | null | undefined) {
  if (!value) return t("Not verified");
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return t("Not verified");
  return parsed.toLocaleString();
}

export function agentStateLabel(state: string | undefined) {
  if (state === "ok") return t("Agent OK");
  if (state === "degraded") return t("Agent with failures");
  return t("Agent without status");
}
