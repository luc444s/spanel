import type { MailCertificate } from "../../api";

export const STATUS_LABELS: Record<MailCertificate["status"], string> = {
  active: "Válido",
  expiring: "Por vencer",
  expired: "Vencido",
  missing: "Sin certificado",
  error: "Error",
};

export function statusTone(status: MailCertificate["status"]) {
  if (status === "active") return "text-emerald-600";
  if (status === "expiring") return "text-amber-600";
  if (status === "expired" || status === "error") return "text-destructive";
  return "text-muted-foreground";
}

export function sourceLabel(cert: MailCertificate) {
  if (cert.scope === "global") return "Global · Predeterminado";
  const source =
    cert.source === "certbot" ? "Certbot" : cert.source === "step-ca" ? "step-ca" : "Estático";
  if (cert.deployment === "sni") return `${source} · SNI`;
  if (cert.deployment === "none") return `${source} · Sin desplegar`;
  if (cert.deployment === "failed") return `${source} · Falló`;
  return `${source} · Predeterminado`;
}

export function deploymentLabel(cert: MailCertificate) {
  if (cert.deployment === "sni") return "SNI";
  if (cert.deployment === "default") return "Predeterminado";
  if (cert.deployment === "none") return "Sin desplegar";
  return "Falló";
}

export function expiryLabel(cert: MailCertificate) {
  if (!cert.not_after) return "Sin fecha";
  const target = new Date(cert.not_after).getTime();
  if (Number.isNaN(target)) return "Sin fecha";
  const days = Math.ceil((target - Date.now()) / 86_400_000);
  if (days < 0) return `Venció hace ${Math.abs(days)} ${Math.abs(days) === 1 ? "día" : "días"}`;
  if (days === 0) return "Vence hoy";
  return `Vence en ${days} ${days === 1 ? "día" : "días"}`;
}

export function timestampLabel(value: string | null | undefined) {
  if (!value) return "Sin verificar";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Sin verificar";
  return parsed.toLocaleString();
}

export function agentStateLabel(state: string | undefined) {
  if (state === "ok") return "Agente OK";
  if (state === "degraded") return "Agente con fallos";
  return "Agente sin estado";
}
