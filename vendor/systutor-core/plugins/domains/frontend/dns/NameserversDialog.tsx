import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { t } from "@/i18n";
import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { ApiError } from "@systutor/shell/api/client";
import { applyNameservers, dnsKeys, getNameservers, planNameservers, verifyNameservers } from "./api";
import { buildNameserverApply, canApplyNameserverPlan, canConfigureNameservers, safeErrorMessage } from "./logic";
import type { NameserverPlan, NameserverSettings, VerifyResult, ZoneItem } from "./types";

const blank: NameserverSettings = {
  ns1: "", ns2: "", ns1_ipv4: null, ns1_ipv6: null,
  ns2_ipv4: null, ns2_ipv6: null, soa_contact: "", default_ttl: 300,
};

function answers(value: unknown) {
  return Array.isArray(value) ? value.map(String) : [];
}

export function NameserversDialog({ open, onClose, permissions, zones }: {
  open: boolean;
  onClose: () => void;
  permissions: string[];
  zones: ZoneItem[];
}) {
  const client = useQueryClient();
  const configurable = canConfigureNameservers(permissions);
  const query = useQuery({ queryKey: dnsKeys.nameservers, queryFn: getNameservers, enabled: open, retry: false });
  const [configure, setConfigure] = useState(false);
  const [settings, setSettings] = useState(blank);
  const [plan, setPlan] = useState<NameserverPlan | null>(null);
  const [approved, setApproved] = useState<string[]>([]);
  const [verifyZone, setVerifyZone] = useState("");
  const [resolver, setResolver] = useState("127.0.0.1");
  const [port, setPort] = useState(53);
  const [verification, setVerification] = useState<VerifyResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!query.data) return;
    setSettings({
      ns1: query.data.ns1 ?? "", ns2: query.data.ns2 ?? "",
      ns1_ipv4: query.data.ns1_ipv4 ?? null, ns1_ipv6: query.data.ns1_ipv6 ?? null,
      ns2_ipv4: query.data.ns2_ipv4 ?? null, ns2_ipv6: query.data.ns2_ipv6 ?? null,
      soa_contact: query.data.soa_contact ?? "", default_ttl: query.data.default_ttl ?? 300,
    });
  }, [query.data]);
  useEffect(() => { if (!verifyZone && zones[0]) setVerifyZone(zones[0].name); }, [verifyZone, zones]);

  const planning = useMutation({
    mutationFn: () => planNameservers(settings, zones.map((zone) => zone.name)),
    onSuccess: (result) => {
      setPlan(result);
      setApproved(result.zones.filter((zone) => zone.drift).map((zone) => zone.zone ?? zone.name ?? "").filter(Boolean));
      setError(null);
    },
    onError: (reason) => setError(safeErrorMessage(reason)),
  });
  const applying = useMutation({
    mutationFn: () => applyNameservers(buildNameserverApply(settings, plan!, approved)),
    onSuccess: () => {
      setPlan(null); setConfigure(false);
      void client.invalidateQueries({ queryKey: dnsKeys.nameservers });
      void client.invalidateQueries({ queryKey: dnsKeys.zones });
    },
    onError: (reason) => {
      if (reason instanceof ApiError && reason.status === 409) {
        setPlan(null);
        setApproved([]);
        setError(t("Nameserver settings or a zone changed. Create a fresh plan; nothing was applied."));
      } else {
        setError(safeErrorMessage(reason));
      }
    },
  });
  const verifying = useMutation({
    mutationFn: () => verifyNameservers(verifyZone, resolver, port),
    onSuccess: (result) => { setVerification(result); setError(null); },
    onError: (reason) => { setVerification(null); setError(safeErrorMessage(reason)); },
  });

  const field = (key: keyof NameserverSettings, label: string, type = "text") => <label className="space-y-1 text-xs">
    <span>{t(label)}</span><Input type={type} value={settings[key] ?? ""} onChange={(event) => {
      const value = type === "number" ? Number(event.target.value) : event.target.value || null;
      setSettings({ ...settings, [key]: value }); setPlan(null);
    }} /></label>;

  return <Dialog open={open} title={t("Public nameservers")} onClose={onClose} maxWidthClassName="max-w-[800px]">
    {query.isLoading ? <p>{t("Loading nameservers...")}</p> : query.isError ? <Alert title={t("Could not load nameservers")}><Button size="sm" onClick={() => void query.refetch()}>{t("Retry")}</Button></Alert> : <div className="space-y-5">
      {query.data && <div className="grid gap-2 text-sm sm:grid-cols-2">
        <span>{t("ns1")}: {query.data.ns1 ?? "—"}</span><span>{t("ns2")}: {query.data.ns2 ?? "—"}</span>
        <span>{t("A / AAAA")}: {[query.data.ns1_ipv4, query.data.ns1_ipv6].filter(Boolean).join(" · ") || "—"}</span>
        <span>{t("A / AAAA")}: {[query.data.ns2_ipv4, query.data.ns2_ipv6].filter(Boolean).join(" · ") || "—"}</span>
        <span>{t("SOA contact")}: {query.data.soa_contact ?? "—"}</span><span>{t("TTL")}: {query.data.default_ttl ?? "—"} · {t("settings version {version}", { version: query.data.version })}</span>
      </div>}
      <Alert title={t("Not physical redundancy")}>{t("ns1 and ns2 may share one address. This is not physical redundancy.")}</Alert>
      <section className="space-y-3 border-t border-border pt-4"><h3 className="font-medium">{t("Verify nameservers")}</h3>
        <div className="grid gap-2 sm:grid-cols-3"><label className="text-xs">{t("Zone")}<select className="mt-1 w-full rounded-md border border-input bg-background p-2" value={verifyZone} onChange={(e) => setVerifyZone(e.target.value)}>{zones.map((zone) => <option key={zone.name}>{zone.name}</option>)}</select></label>
          <label className="text-xs">{t("Resolver")}<Input value={resolver} onChange={(e) => setResolver(e.target.value)} /></label>
          <label className="text-xs">{t("Port")}<Input type="number" value={port} onChange={(e) => setPort(Number(e.target.value))} /></label></div>
        <Button size="sm" disabled={!verifyZone || verifying.isPending} onClick={() => verifying.mutate()}>{t("Verify UDP/TCP")}</Button>
        {verification && <div className="grid gap-3 sm:grid-cols-2">{["udp", "tcp"].map((transport) => {
          const result = verification.transports[transport] ?? {};
          const soa = answers(result.soa);
          const ns = answers(result.ns);
          return <section className="space-y-2 rounded border border-border p-3 text-xs" key={transport}>
            <div className="flex items-center justify-between"><strong>{transport.toUpperCase()}</strong><span>{t("Authoritative")}: {t(result.authoritative ? "Yes" : "No")}</span></div>
            <div><strong>{t("SOA answers")}</strong>{soa.length ? <ul>{soa.map((answer) => <li className="break-all" key={answer}>{answer}</li>)}</ul> : <p>{t("No answers")}</p>}</div>
            <div><strong>{t("NS answers")}</strong>{ns.length ? <ul>{ns.map((answer) => <li className="break-all" key={answer}>{answer}</li>)}</ul> : <p>{t("No answers")}</p>}</div>
          </section>;
        })}</div>}
      </section>
      {configurable && !configure && <Button onClick={() => setConfigure(true)}>{t("Configure")}</Button>}
      {configurable && configure && <section className="space-y-3 border-t border-border pt-4"><h3 className="font-medium">{t("Configure nameservers")}</h3>
        <div className="grid gap-2 sm:grid-cols-3">{field("ns1", "ns1")}{field("ns1_ipv4", "ns1 IPv4")}{field("ns1_ipv6", "ns1 IPv6")}{field("ns2", "ns2")}{field("ns2_ipv4", "ns2 IPv4")}{field("ns2_ipv6", "ns2 IPv6")}{field("soa_contact", "SOA contact")}{field("default_ttl", "Default TTL", "number")}</div>
        <Button disabled={planning.isPending} onClick={() => planning.mutate()}>{t("Plan")}</Button>
        {plan && <div className="space-y-2 rounded border border-border p-3 text-sm"><strong>{t("Plan result")}</strong>
          <p>{t("Settings changes: {fields}", { fields: plan.settings_fields.join(", ") || t("none") })}</p>
          {plan.zones.map((zone) => { const name = zone.zone ?? zone.name ?? ""; return <label className="flex gap-2" key={name}><input type="checkbox" checked={approved.includes(name)} onChange={(event) => setApproved(event.target.checked ? [...approved, name] : approved.filter((item) => item !== name))} /><span>{name} · {t("version {version}", { version: zone.version ?? "—" })} · {t(zone.drift ? "SOA/NS change: yes" : "SOA/NS change: no")}</span></label>; })}
          {!zones.length && <p>{t("No owned zones yet. The settings will be saved without reconciling any zone. This is not \"apply to all zones\".")}</p>}
          <p>{t("{count} zones approved. Registrar delegation and Cloudflare are not touched.", { count: approved.length })}</p>
          <Button disabled={applying.isPending || !canApplyNameserverPlan(plan, approved, zones.length > 0)} onClick={() => applying.mutate()}>{t(zones.length ? "Apply plan" : "Apply settings")}</Button>
        </div>}
      </section>}
      {error && <Alert title={t("DNS operation failed")}>{error}</Alert>}
    </div>}
  </Dialog>;
}
