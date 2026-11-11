import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@systutor/shell/ui/button";
import { Input } from "@systutor/shell/ui/input";

import { t } from "@/i18n";
import { useT } from "@/i18n/use-i18n";

import { revealManagedSecret } from "../api";
import type { ServiceDetail } from "../types";
import { SecretRevealDialog } from "./SecretRevealDialog";

const SOURCE = { managed: "Managed", tenant: "Tenant", image: "Image" } as const;

export function sourceLabel(source: keyof typeof SOURCE) {
  return t(SOURCE[source]);
}

export function canRevealVariable(
  variable: ServiceDetail["environment"][number], canReveal: boolean,
) {
  return variable.revealable && canReveal;
}

export function revealExpiry(now = Date.now()) {
  return now + 30_000;
}

export function remainingSeconds(expires: number, now = Date.now()) {
  return Math.max(0, Math.ceil((expires - now) / 1000));
}

export function ServiceEnvironment({ applicationId, service, canReveal }: {
  applicationId: string;
  service: ServiceDetail;
  canReveal: boolean;
}) {
  const t = useT();
  const [search, setSearch] = useState("");
  const [secretsOnly, setSecretsOnly] = useState(false);
  const [target, setTarget] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<{ name: string; value: string; expires: number } | null>(null);
  const [remaining, setRemaining] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  function hide() { if (timeout.current) clearTimeout(timeout.current); timeout.current = null; setRevealed(null); setRemaining(0); }
  useEffect(() => hide, []);
  useEffect(() => {
    if (!revealed) return;
    const interval = setInterval(() => setRemaining(remainingSeconds(revealed.expires)), 250);
    return () => clearInterval(interval);
  }, [revealed]);
  const rows = useMemo(() => service.environment.filter((item) =>
    item.name.toLowerCase().includes(search.toLowerCase()) && (!secretsOnly || item.secret)), [service.environment, search, secretsOnly]);
  async function reveal(reason: string) {
    if (!target) return;
    setPending(true); setError(null);
    try {
      const response = await revealManagedSecret(applicationId, target, reason);
      const expires = revealExpiry();
      setRevealed({ name: response.name, value: response.value, expires }); setRemaining(30); setTarget(null);
      timeout.current = setTimeout(hide, 30_000);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("The request was rejected"));
    } finally { setPending(false); }
  }
  return <div className="space-y-3">
    <div className="flex flex-wrap items-end gap-2"><Input aria-label={t("Search variable")} placeholder={t("Search variable...")} value={search} onChange={(event) => setSearch(event.target.value)} /><label className="flex items-center gap-2 text-sm"><span className="text-muted-foreground">{t("Secrets only")}</span><select className="rounded-md border border-input bg-background px-2 py-2" value={secretsOnly ? "secrets" : "all"} onChange={(event) => setSecretsOnly(event.target.value === "secrets")}><option value="all">{t("All variables")}</option><option value="secrets">{t("Secrets only")}</option></select></label></div>
    <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr><th className="p-2">{t("Variable")}</th><th className="p-2">{t("Value")}</th><th className="p-2">{t("Source")}</th><th className="p-2">{t("Actions")}</th></tr></thead><tbody>{rows.map((item) => {
      const visible = revealed?.name === item.name;
      return <tr className="border-t border-border" key={item.name}><td className="p-2"><code>{item.name}</code></td><td className="p-2">{visible ? revealed.value : item.secret ? "••••••••••••" : item.source === "image" ? t("Protected by platform") : t("Configured")}</td><td className="p-2">{sourceLabel(item.source)}</td><td className="p-2">{visible ? <span className="flex items-center gap-1"><Button variant="secondary" onClick={() => void navigator.clipboard.writeText(revealed.value)}>{t("Copy")}</Button><Button variant="secondary" onClick={hide}>{t("Hide")}</Button><span>{String(remaining).padStart(2, "0")} s</span></span> : canRevealVariable(item, canReveal) ? <Button variant="secondary" onClick={() => { setError(null); setTarget(item.name); }}>{t("View")}</Button> : "—"}</td></tr>;
    })}</tbody></table></div>
    <SecretRevealDialog key={target ?? "closed"} name={target} pending={pending} error={error} onClose={() => { setTarget(null); setError(null); }} onReveal={(reason) => void reveal(reason)} />
  </div>;
}
