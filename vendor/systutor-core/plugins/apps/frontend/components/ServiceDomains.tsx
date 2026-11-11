import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { toast } from "@systutor/shell/ui/toast";

import { t } from "@/i18n";
import { useT } from "@/i18n/use-i18n";

import {
  createRoute,
  domainsKeys,
  getCertificate,
  listAvailableDomains,
  listRoutes,
  probeRoute,
  removeRoute,
  updateRoute,
  type DomainRoute,
  type DomainRoutePayload,
} from "../../../domains/frontend/api";
import type { ServiceDetail } from "../types";

type FormState = {
  domain: string;
  path: string;
  internal_path: string;
  strip_path: boolean;
  port: string;
  entrypoints: string[];
  https: boolean;
  middlewares: string[];
};

export type RouteForm = FormState;

const EMPTY: FormState = {
  domain: "", path: "/", internal_path: "/", strip_path: false, port: "",
  entrypoints: [], https: true, middlewares: [],
};

export function serviceRoutePreview(form: FormState, service: ServiceDetail) {
  const scheme = form.https ? "https" : "http";
  const external = form.path === "/" ? "" : form.path;
  const port = form.port || "—";
  const target = form.internal_path === "/" ? "/*" : `${form.internal_path}/*`;
  return {
    external: `${scheme}://${form.domain || t("domain")}${external}/*`,
    target: `${service.name}:${port}${target}`,
    strip: form.strip_path && form.path !== "/" ? form.path : null,
  };
}

export function routePayload(form: FormState): Omit<DomainRoutePayload, "site_id">;
export function routePayload(form: FormState, siteId: string): DomainRoutePayload;
export function routePayload(form: FormState, siteId?: string): DomainRoutePayload | Omit<DomainRoutePayload, "site_id"> {
  const body = {
    domain: form.domain,
    path: form.path || "/",
    internal_path: form.internal_path || "/",
    strip_path: form.strip_path,
    port: Number(form.port),
    entrypoints: form.entrypoints,
    https: form.https,
    middlewares: form.middlewares,
  };
  return siteId ? { ...body, site_id: siteId } : body;
}

export function ServiceDomains({ applicationId, service, canManage }: {
  applicationId: string;
  service: ServiceDetail;
  canManage: boolean;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const siteId = `${applicationId}:${service.name}`;
  const routesQuery = useQuery({
    queryKey: domainsKeys.routes(siteId),
    queryFn: () => listRoutes(siteId),
  });
  const availableQuery = useQuery({
    queryKey: domainsKeys.available,
    queryFn: listAvailableDomains,
  });
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<DomainRoute | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [formError, setFormError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const [removing, setRemoving] = useState<DomainRoute | null>(null);
  const [probe, setProbe] = useState<string | null>(null);
  const [probePending, setProbePending] = useState(false);

  const routes = routesQuery.data?.items ?? [];
  const available = availableQuery.data;
  const registered = useMemo(
    () => Array.from(new Set((available?.items ?? []).map((item) => item.domain))),
    [available?.items],
  );
  const certificateDomain = routes.find((route) => route.https)?.domain ?? "";
  const certificateQuery = useQuery({
    queryKey: domainsKeys.certificate(certificateDomain),
    queryFn: () => getCertificate(certificateDomain),
    enabled: Boolean(certificateDomain),
    retry: false,
    staleTime: 60_000,
  });

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: domainsKeys.routes() });
    void queryClient.invalidateQueries({ queryKey: domainsKeys.available });
  }

  const saveMutation = useMutation({
    mutationFn: (payload: FormState) => {
      return editing
        ? updateRoute(editing.id, routePayload(payload))
        : createRoute(routePayload(payload, siteId));
    },
    onSuccess: () => {
      toast.success(editing ? t("Route updated") : t("Route published"));
      invalidate();
      setFormOpen(false);
      setEditing(null);
      setFormError(null);
    },
    onError: (error: Error & { status?: number }) => {
      if (error.status === 409) {
        setFormOpen(false);
        setConflict(error.message || t("The route is already assigned."));
        return;
      }
      setFormError(error.message);
    },
  });

  const removeMutation = useMutation({
    mutationFn: (route: DomainRoute) => removeRoute(route.id),
    onSuccess: () => {
      toast.success(t("Route deleted"));
      invalidate();
      setRemoving(null);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  function openCreate() {
    setEditing(null);
    setFormError(null);
    setForm({
      ...EMPTY,
      domain: registered[0] ?? "",
      port: String(service.ports[0] ?? ""),
      entrypoints: [...(available?.entrypoints ?? [])],
    });
    setFormOpen(true);
  }

  function openEdit(route: DomainRoute) {
    setEditing(route);
    setFormError(null);
    setForm({
      domain: route.domain,
      path: route.path,
      internal_path: route.internal_path,
      strip_path: route.strip_path,
      port: String(route.port),
      entrypoints: [...route.entrypoints],
      https: route.https,
      middlewares: [...route.middlewares],
    });
    setFormOpen(true);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    if (!form.domain) {
      setFormError(t("Choose a registered domain."));
      return;
    }
    if (!form.port) {
      setFormError(t("Enter the container port."));
      return;
    }
    saveMutation.mutate(form);
  }

  async function runProbe() {
    if (!editing) return;
    setProbePending(true);
    setProbe(null);
    try {
      const result = await probeRoute(editing.id);
      const status = result.http_status ?? t("no response");
      const certificate = result.certificate?.verified ? ` · ${t("certificate verified")}` : "";
      setProbe(t("Route responded {status}{certificate}", { status, certificate }));
    } catch (error) {
      setProbe(error instanceof Error ? error.message : t("The probe was rejected"));
    } finally {
      setProbePending(false);
    }
  }

  const currentPreview = serviceRoutePreview(form, service);
  const portDetected = service.ports.includes(Number(form.port));
  const remainingMiddlewares = (available?.middlewares ?? []).filter(
    (item) => !form.middlewares.includes(item),
  );

  return <div className="space-y-4">
    {routesQuery.isError ? <Alert title={t("Could not load routes")}>{(routesQuery.error as Error).message}</Alert> : null}
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-medium">{t("Published routes")}</h3>
      {canManage ? <Button onClick={openCreate}>{t("Assign registered domain")}</Button> : null}
    </div>
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px] text-left text-sm">
        <thead><tr><th className="p-2">{t("Host")}</th><th className="p-2">{t("Path")}</th><th className="p-2">{t("Destination")}</th><th className="p-2">{t("HTTPS")}</th><th className="p-2">{t("Status")}</th><th className="p-2">{t("Actions")}</th></tr></thead>
        <tbody>{routes.map((route) => <tr className="border-t border-border" key={route.id}>
          <td className="p-2">{route.domain}</td><td className="p-2"><code>{route.path}</code></td>
          <td className="p-2"><code>{service.name}:{route.port}</code></td><td className="p-2">{route.https ? t("Yes") : t("No")}</td>
          <td className="p-2">{route.available ? t("● Active") : t("Pending")}</td>
          <td className="p-2">{canManage ? <span className="flex flex-wrap gap-1"><Button variant="secondary" onClick={() => openEdit(route)}>{t("Edit")}</Button><Button variant="secondary" onClick={() => setRemoving(route)}>{t("Remove")}</Button></span> : "—"}</td>
        </tr>)}</tbody>
      </table>
    </div>
    {routes.length === 0 ? <p className="text-sm text-muted-foreground">{t("There are no routes for {name} yet.", { name: service.name })}</p> : null}
    {certificateDomain ? <div className="rounded border border-border p-3 text-sm">
      <p className="font-medium">{t("Certificate for {domain}", { domain: certificateDomain })}</p>
      <p className="mt-1 text-muted-foreground">{certificateQuery.data ? <>{certificateQuery.data.issuer ?? t("Unknown issuer")} · {certificateQuery.data.not_after ? t("valid until {date}", { date: new Date(certificateQuery.data.not_after).toLocaleDateString() }) : t("no expiration reported")} · {certificateQuery.data.verified ? t("Verified ✓") : certificateQuery.data.state}</> : t("No certificate data.")}</p>
    </div> : null}
    <a className="inline-block text-sm text-primary underline" href="/app/domains" target="_top">{t("Manage domains ↗")}</a>

    <Dialog open={formOpen} title={editing ? t("Edit domain for {name}", { name: service.name }) : t("Assign domain to {name}", { name: service.name })} onClose={() => { setFormOpen(false); setEditing(null); }} zIndexClassName="z-[1200]">
      <form className="space-y-3" onSubmit={submit}>
        <label className="block space-y-1 text-sm"><span>{t("Registered domain *")}</span>
          <select className="w-full rounded-md border border-input bg-background px-2 py-2" value={form.domain} onChange={(event) => setForm({ ...form, domain: event.target.value })}>
            <option value="">{t("Choose domain")}</option>
            {registered.map((domain) => <option key={domain} value={domain}>{domain}</option>)}
          </select>
        </label>
        {registered.length === 0 ? <Alert title={t("No registered domains")}>{t("Create or reserve a domain in Domains before assigning it.")}</Alert> : null}
        <label className="block space-y-1 text-sm"><span>{t("External path")}</span><Input value={form.path} onChange={(event) => setForm({ ...form, path: event.target.value, strip_path: event.target.value === "/" ? false : form.strip_path })} /></label>
        <label className="block space-y-1 text-sm"><span>{t("Internal Path")}</span><Input value={form.internal_path} onChange={(event) => setForm({ ...form, internal_path: event.target.value })} /></label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.strip_path} disabled={form.path === "/"} onChange={(event) => setForm({ ...form, strip_path: event.target.checked })} />{t("Strip Path")}</label>
        <p className="text-xs text-muted-foreground">{t("Removes {path} before forwarding the request.", { path: form.path })}</p>
        <label className="block space-y-1 text-sm"><span>{t("Container Port *")}</span><Input type="number" min={1} max={65535} value={form.port} onChange={(event) => setForm({ ...form, port: event.target.value })} /></label>
        <p className="text-xs text-muted-foreground">{t("Detected ports: {ports}", { ports: service.ports.join(", ") || "—" })}</p>
        <fieldset className="space-y-1 text-sm"><legend>{t("Custom Entrypoints")}</legend>
          {(available?.entrypoints ?? []).map((entrypoint) => <label className="mr-3 inline-flex items-center gap-1" key={entrypoint}><input type="checkbox" checked={form.entrypoints.includes(entrypoint)} onChange={(event) => setForm({ ...form, entrypoints: event.target.checked ? [...form.entrypoints, entrypoint] : form.entrypoints.filter((item) => item !== entrypoint) })} />{entrypoint}</label>)}
          {(available?.entrypoints ?? []).length === 0 ? <span className="text-muted-foreground">{t("Domains did not report entrypoints.")}</span> : null}
        </fieldset>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.https} onChange={(event) => setForm({ ...form, https: event.target.checked })} />{t("Provision certificate automatically")}</label>
        <div className="space-y-2 text-sm"><p>{t("Middlewares")}</p>
          <div className="flex flex-wrap gap-2">
            <select className="rounded-md border border-input bg-background px-2 py-2" value="" onChange={(event) => { if (event.target.value) setForm({ ...form, middlewares: [...form.middlewares, event.target.value] }); }}>
              <option value="">{t("Choose middleware")}</option>
              {remainingMiddlewares.map((middleware) => <option key={middleware} value={middleware}>{middleware}</option>)}
            </select>
          </div>
          <div className="flex flex-wrap gap-1">{form.middlewares.map((middleware) => <button type="button" className="rounded bg-muted px-2 py-1 text-xs" key={middleware} onClick={() => setForm({ ...form, middlewares: form.middlewares.filter((item) => item !== middleware) })}>{middleware} ×</button>)}</div>
        </div>
        <div className="rounded border border-border p-3 text-sm"><p className="font-medium">{t("PREVIEW")}</p><p className="mt-1 break-all">{currentPreview.external}</p>{currentPreview.strip ? <p className="text-xs text-muted-foreground">{t("↓ Strip {path}", { path: currentPreview.strip })}</p> : null}<p className="break-all">{currentPreview.target}</p></div>
        <div className="rounded border border-border p-3 text-xs text-muted-foreground"><p className="font-medium text-foreground">{t("VALIDATION")}</p><p>{form.domain ? "✓" : "✗"} {t("The domain belongs to the tenant")}</p><p>{t("Domains validates the host + path conflict on save.")}</p><p>{portDetected ? "✓" : "✗"} {t("The port is declared by {name}", { name: service.name })}</p><p>✓ {t("Domains can reach the service")}</p></div>
        {formError ? <Alert title={t("Could not save the route")}>{formError}</Alert> : null}
        {probe ? <Alert title={t("Probe result")}>{probe}</Alert> : null}
        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" variant="secondary" onClick={() => { setFormOpen(false); setEditing(null); }}>{t("Cancel")}</Button>
          <Button type="button" variant="secondary" disabled={!editing || probePending} title={editing ? undefined : t("Save the route to test it")} onClick={() => void runProbe()}>{t("Test route")}</Button>
          <Button type="submit" disabled={saveMutation.isPending || !form.domain}>{t("Save and publish")}</Button>
        </div>
      </form>
    </Dialog>

    <Dialog open={Boolean(conflict)} title={t("Cannot publish the route")} onClose={() => setConflict(null)} zIndexClassName="z-[1200]">
      <p className="text-sm">{conflict}</p>
      <p className="mt-2 text-sm text-muted-foreground">{t("Change the domain or path before continuing.")}</p>
      <div className="mt-4 flex justify-end"><Button onClick={() => setConflict(null)}>{t("Close")}</Button></div>
    </Dialog>

    <Dialog open={Boolean(removing)} title={t("Remove route")} description={removing ? `${removing.domain} ${removing.path}` : undefined} onClose={() => setRemoving(null)} zIndexClassName="z-[1200]">
      <p className="text-sm text-muted-foreground">{t("The route is removed from the proxy. The registered domain and other services are unchanged.")}</p>
      <div className="mt-4 flex justify-end gap-2"><Button variant="secondary" onClick={() => setRemoving(null)}>{t("Cancel")}</Button><Button className="text-destructive" disabled={removeMutation.isPending} onClick={() => removing && removeMutation.mutate(removing)}>{t("Remove route")}</Button></div>
    </Dialog>
  </div>;
}
