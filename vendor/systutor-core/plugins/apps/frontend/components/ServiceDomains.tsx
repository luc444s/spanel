import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { toast } from "@systutor/shell/ui/toast";

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
    external: `${scheme}://${form.domain || "dominio"}${external}/*`,
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
      toast.success(editing ? "Ruta actualizada" : "Ruta publicada");
      invalidate();
      setFormOpen(false);
      setEditing(null);
      setFormError(null);
    },
    onError: (error: Error & { status?: number }) => {
      if (error.status === 409) {
        setFormOpen(false);
        setConflict(error.message || "La ruta ya está asignada.");
        return;
      }
      setFormError(error.message);
    },
  });

  const removeMutation = useMutation({
    mutationFn: (route: DomainRoute) => removeRoute(route.id),
    onSuccess: () => {
      toast.success("Ruta eliminada");
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
      setFormError("Elegí un dominio registrado.");
      return;
    }
    if (!form.port) {
      setFormError("Indicá el puerto del contenedor.");
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
      const status = result.http_status ?? "sin respuesta";
      const certificate = result.certificate?.verified ? " · certificado verificado" : "";
      setProbe(`Ruta respondió ${status}${certificate}`);
    } catch (error) {
      setProbe(error instanceof Error ? error.message : "El probe fue rechazado");
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
    {routesQuery.isError ? <Alert title="No se pudieron cargar las rutas">{(routesQuery.error as Error).message}</Alert> : null}
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-medium">Rutas publicadas</h3>
      {canManage ? <Button size="sm" onClick={openCreate}>Asignar dominio registrado</Button> : null}
    </div>
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px] text-left text-sm">
        <thead><tr><th className="p-2">Host</th><th className="p-2">Path</th><th className="p-2">Destino</th><th className="p-2">HTTPS</th><th className="p-2">Estado</th><th className="p-2">Acciones</th></tr></thead>
        <tbody>{routes.map((route) => <tr className="border-t border-border" key={route.id}>
          <td className="p-2">{route.domain}</td><td className="p-2"><code>{route.path}</code></td>
          <td className="p-2"><code>{service.name}:{route.port}</code></td><td className="p-2">{route.https ? "Sí" : "No"}</td>
          <td className="p-2">{route.available ? "● Activa" : "Pendiente"}</td>
          <td className="p-2">{canManage ? <span className="flex flex-wrap gap-1"><Button size="sm" variant="secondary" onClick={() => openEdit(route)}>Editar</Button><Button size="sm" variant="secondary" onClick={() => setRemoving(route)}>Quitar</Button></span> : "—"}</td>
        </tr>)}</tbody>
      </table>
    </div>
    {routes.length === 0 ? <p className="text-sm text-muted-foreground">Todavía no hay rutas para {service.name}.</p> : null}
    {certificateDomain ? <div className="rounded border border-border p-3 text-sm">
      <p className="font-medium">Certificado de {certificateDomain}</p>
      <p className="mt-1 text-muted-foreground">{certificateQuery.data ? <>{certificateQuery.data.issuer ?? "Emisor desconocido"} · {certificateQuery.data.not_after ? `válido hasta ${new Date(certificateQuery.data.not_after).toLocaleDateString()}` : "sin vencimiento informado"} · {certificateQuery.data.verified ? "Verificado ✓" : certificateQuery.data.state}</> : "Sin datos de certificado."}</p>
    </div> : null}
    <a className="inline-block text-sm text-primary underline" href="/app/domains" target="_top">Administrar dominios ↗</a>

    <Dialog open={formOpen} title={editing ? `Editar dominio de ${service.name}` : `Asignar dominio a ${service.name}`} onClose={() => { setFormOpen(false); setEditing(null); }} zIndexClassName="z-[1200]">
      <form className="space-y-3" onSubmit={submit}>
        <label className="block space-y-1 text-sm"><span>Dominio registrado *</span>
          <select className="w-full rounded-md border border-input bg-background px-2 py-2" value={form.domain} onChange={(event) => setForm({ ...form, domain: event.target.value })}>
            <option value="">Elegir dominio</option>
            {registered.map((domain) => <option key={domain} value={domain}>{domain}</option>)}
          </select>
        </label>
        {registered.length === 0 ? <Alert title="Sin dominios registrados">Creá o reservá un dominio en Dominios antes de asignarlo.</Alert> : null}
        <label className="block space-y-1 text-sm"><span>Path externo</span><Input value={form.path} onChange={(event) => setForm({ ...form, path: event.target.value, strip_path: event.target.value === "/" ? false : form.strip_path })} /></label>
        <label className="block space-y-1 text-sm"><span>Internal Path</span><Input value={form.internal_path} onChange={(event) => setForm({ ...form, internal_path: event.target.value })} /></label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.strip_path} disabled={form.path === "/"} onChange={(event) => setForm({ ...form, strip_path: event.target.checked })} />Strip Path</label>
        <p className="text-xs text-muted-foreground">Elimina <code>{form.path}</code> antes de reenviar la solicitud.</p>
        <label className="block space-y-1 text-sm"><span>Container Port *</span><Input type="number" min={1} max={65535} value={form.port} onChange={(event) => setForm({ ...form, port: event.target.value })} /></label>
        <p className="text-xs text-muted-foreground">Puertos detectados: {service.ports.join(", ") || "—"}</p>
        <fieldset className="space-y-1 text-sm"><legend>Custom Entrypoints</legend>
          {(available?.entrypoints ?? []).map((entrypoint) => <label className="mr-3 inline-flex items-center gap-1" key={entrypoint}><input type="checkbox" checked={form.entrypoints.includes(entrypoint)} onChange={(event) => setForm({ ...form, entrypoints: event.target.checked ? [...form.entrypoints, entrypoint] : form.entrypoints.filter((item) => item !== entrypoint) })} />{entrypoint}</label>)}
          {(available?.entrypoints ?? []).length === 0 ? <span className="text-muted-foreground">Dominios no informó entrypoints.</span> : null}
        </fieldset>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.https} onChange={(event) => setForm({ ...form, https: event.target.checked })} />Provisionar certificado automáticamente</label>
        <div className="space-y-2 text-sm"><p>Middlewares</p>
          <div className="flex flex-wrap gap-2">
            <select className="rounded-md border border-input bg-background px-2 py-2" value="" onChange={(event) => { if (event.target.value) setForm({ ...form, middlewares: [...form.middlewares, event.target.value] }); }}>
              <option value="">Elegir middleware</option>
              {remainingMiddlewares.map((middleware) => <option key={middleware} value={middleware}>{middleware}</option>)}
            </select>
          </div>
          <div className="flex flex-wrap gap-1">{form.middlewares.map((middleware) => <button type="button" className="rounded bg-muted px-2 py-1 text-xs" key={middleware} onClick={() => setForm({ ...form, middlewares: form.middlewares.filter((item) => item !== middleware) })}>{middleware} ×</button>)}</div>
        </div>
        <div className="rounded border border-border p-3 text-sm"><p className="font-medium">PREVIEW</p><p className="mt-1 break-all">{currentPreview.external}</p>{currentPreview.strip ? <p className="text-xs text-muted-foreground">↓ Strip {currentPreview.strip}</p> : null}<p className="break-all">{currentPreview.target}</p></div>
        <div className="rounded border border-border p-3 text-xs text-muted-foreground"><p className="font-medium text-foreground">VALIDACIÓN</p><p>{form.domain ? "✓" : "✗"} El dominio pertenece al tenant</p><p>Dominios valida el conflicto de host + path al guardar.</p><p>{portDetected ? "✓" : "✗"} El puerto está declarado por {service.name}</p><p>✓ Dominios puede alcanzar el servicio</p></div>
        {formError ? <Alert title="No se pudo guardar la ruta">{formError}</Alert> : null}
        {probe ? <Alert title="Resultado del probe">{probe}</Alert> : null}
        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" size="sm" variant="secondary" onClick={() => { setFormOpen(false); setEditing(null); }}>Cancelar</Button>
          <Button type="button" size="sm" variant="secondary" disabled={!editing || probePending} title={editing ? undefined : "Guardá la ruta para probarla"} onClick={() => void runProbe()}>Probar ruta</Button>
          <Button type="submit" size="sm" disabled={saveMutation.isPending || !form.domain}>Guardar y publicar</Button>
        </div>
      </form>
    </Dialog>

    <Dialog open={Boolean(conflict)} title="No se puede publicar la ruta" onClose={() => setConflict(null)} zIndexClassName="z-[1200]">
      <p className="text-sm">{conflict}</p>
      <p className="mt-2 text-sm text-muted-foreground">Cambiá el dominio o el path antes de continuar.</p>
      <div className="mt-4 flex justify-end"><Button size="sm" onClick={() => setConflict(null)}>Cerrar</Button></div>
    </Dialog>

    <Dialog open={Boolean(removing)} title="Quitar ruta" description={removing ? `${removing.domain} ${removing.path}` : undefined} onClose={() => setRemoving(null)} zIndexClassName="z-[1200]">
      <p className="text-sm text-muted-foreground">Se elimina la ruta del proxy. El dominio registrado y los demás servicios no cambian.</p>
      <div className="mt-4 flex justify-end gap-2"><Button size="sm" variant="secondary" onClick={() => setRemoving(null)}>Cancelar</Button><Button size="sm" className="text-destructive" disabled={removeMutation.isPending} onClick={() => removing && removeMutation.mutate(removing)}>Quitar ruta</Button></div>
    </Dialog>
  </div>;
}
