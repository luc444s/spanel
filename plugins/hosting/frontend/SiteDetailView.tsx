import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { apiRequest } from '@systutor/shell'
import { Alert } from '@systutor/shell/ui/alert'
import { Badge } from '@systutor/shell/ui/badge'
import { Button } from '@systutor/shell/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@systutor/shell/ui/card'
import { ConfirmDialog } from '@systutor/shell/ui/confirm-dialog'
import { useAuthz } from '@spanel-app/authz'
import type { Site } from './SitesView'

export type SiteDetail = Site & {
  origin: {
    public_urls: string[]
    backend: string | null
    network: string | null
    host_ip: string
    container_status: string
  }
}

function statusBadgeClass(status: string) {
  if (status === 'running') return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700'
  if (status === 'unreachable') return 'border-amber-500/30 bg-amber-500/10 text-amber-700'
  if (status === 'missing') return 'border-destructive/30 bg-destructive/10 text-destructive'
  if (status === 'exited' || status === 'stopped') {
    return 'border-border bg-secondary text-secondary-foreground'
  }
  return 'border-border bg-secondary text-secondary-foreground'
}

export function SiteDetailView() {
  const navigate = useNavigate()
  const { hasAnyPermission } = useAuthz()
  const { id } = useParams<{ id: string }>()
  const [site, setSite] = useState<SiteDetail | null>(null)
  const [logs, setLogs] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [logsLoading, setLogsLoading] = useState(false)
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const canRuntimeRead = hasAnyPermission(['hosting.runtime.read'])
  const canRuntimeManage = hasAnyPermission(['hosting.runtime.manage'])
  const canFilesManage = hasAnyPermission(['hosting.files.manage'])
  const canDelete = hasAnyPermission(['hosting.sites.delete'])

  const fetchSite = async (siteId: string) => {
    try {
      setError(null)
      setSite(await apiRequest<SiteDetail>(`/api/v1/plugins/hosting/sites/${siteId}`))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!id) return
    setLoading(true)
    setError(null)
    void fetchSite(id)
  }, [id])

  const loadLogs = async () => {
    if (!id) return
    setError(null)
    setLogsLoading(true)
    try {
      const res = await apiRequest<{ lines: string }>(
        `/api/v1/plugins/hosting/sites/${id}/logs?tail=50`,
      )
      setLogs(res.lines)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLogsLoading(false)
    }
  }

  const runAction = async (action: 'start' | 'stop' | 'restart' | 'refresh') => {
    if (!id) return
    setBusyAction(action)
    setError(null)
    try {
      if (action === 'refresh') {
        await fetchSite(id)
      } else {
        await apiRequest(`/api/v1/plugins/hosting/sites/${id}/${action}`, { method: 'POST' })
        await fetchSite(id)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyAction(null)
    }
  }

  const handleDelete = async () => {
    if (!id) return
    setBusyAction('delete')
    setError(null)
    try {
      await apiRequest(`/api/v1/plugins/hosting/sites/${id}`, { method: 'DELETE' })
      navigate('/p/hosting/sites', { replace: true })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyAction(null)
      setDeleteOpen(false)
    }
  }

  if (!site && error) {
    return (
      <div className="space-y-4">
        <Alert title="Error">{error}</Alert>
        <Link to="/p/hosting/sites" className="text-sm text-primary hover:underline">
          ← Volver a sitios
        </Link>
      </div>
    )
  }

  if (loading && !site) {
    return <p className="text-sm text-muted-foreground">Cargando…</p>
  }

  if (!site) return null

  const { origin } = site

  return (
    <div className="space-y-6">
      {error && <Alert title="Error">{error}</Alert>}

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/p/hosting/sites" className="text-sm text-primary hover:underline">
            ← Sitios
          </Link>
          <h1 className="text-lg font-semibold">{site.name}</h1>
          <Badge>{site.stack}</Badge>
          <Badge className={statusBadgeClass(site.container_status)}>{site.container_status}</Badge>
        </div>
        <div className="flex flex-wrap gap-2">
          {canRuntimeManage && (
            <>
              <Button variant="secondary" onClick={() => void runAction('start')} disabled={!!busyAction}>
                {busyAction === 'start' ? 'Iniciando...' : 'Start'}
              </Button>
              <Button variant="secondary" onClick={() => void runAction('stop')} disabled={!!busyAction}>
                {busyAction === 'stop' ? 'Deteniendo...' : 'Stop'}
              </Button>
              <Button variant="secondary" onClick={() => void runAction('restart')} disabled={!!busyAction}>
                {busyAction === 'restart' ? 'Reiniciando...' : 'Restart'}
              </Button>
            </>
          )}
          <Button variant="secondary" onClick={() => void runAction('refresh')} disabled={!!busyAction}>
            {busyAction === 'refresh' ? 'Actualizando...' : 'Actualizar'}
          </Button>
          {canDelete && (
            <Button variant="secondary" className="text-destructive" onClick={() => setDeleteOpen(true)} disabled={!!busyAction}>
              Eliminar de Spanel
            </Button>
          )}
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Origen</CardTitle>
            <CardDescription>De dónde viene este sitio</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div>
              <div className="text-muted-foreground">URL pública</div>
              <div>
                {origin.public_urls.length
                  ? origin.public_urls.map((url) => (
                      <a key={url} href={url} target="_blank" rel="noreferrer" className="text-primary hover:underline block">
                        {url}
                      </a>
                    ))
                  : 'Sin dominios (SP-0011)'}
              </div>
            </div>
            <div>
              <div className="text-muted-foreground">Backend</div>
              <div className="font-mono">{origin.backend ?? '—'}</div>
            </div>
            <div>
              <div className="text-muted-foreground">Red docker</div>
              <div className="font-mono">{origin.network ?? '—'}</div>
            </div>
            <div>
              <div className="text-muted-foreground">Host</div>
              <div className="font-mono">{origin.host_ip}</div>
            </div>
          </CardContent>
        </Card>

        {canRuntimeRead && (
          <Card>
            <CardHeader>
              <CardTitle>Logs</CardTitle>
              <CardDescription>Últimas 50 líneas del container</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <Button variant="secondary" onClick={() => void loadLogs()} disabled={logsLoading}>
                {logsLoading ? 'Cargando...' : 'Cargar logs'}
              </Button>
              {logs !== null && (
                <pre className="max-h-72 overflow-auto rounded-md border border-border bg-secondary p-3 text-xs">
                  {logs || '(sin salida)'}
                </pre>
              )}
            </CardContent>
          </Card>
        )}
      </div>

      {site.stack === 'wordpress' && site.domains.length > 0 && canFilesManage && (
        <Card>
          <CardHeader>
            <CardTitle>Archivos</CardTitle>
            <CardDescription>Filebrowser sobre wp-content, protegido por auth Spanel</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Button
              onClick={async () => {
                try {
                  await apiRequest(`/api/v1/plugins/hosting/sites/${site.id}/files/ensure`, {
                    method: 'POST',
                  })
                  window.open(`http://files.${site.domains[0]}/`, '_blank')
                } catch (e) {
                  setError(e instanceof Error ? e.message : String(e))
                }
              }}
            >
              Abrir archivos
            </Button>
            <p className="text-xs text-muted-foreground">
              El navegador debe enviar el header Authorization con el JWT de Spanel
              (primer acceso: usar cliente que permita headers custom).
            </p>
          </CardContent>
        </Card>
      )}

      <ConfirmDialog
        open={deleteOpen}
        title="Eliminar sitio de Spanel"
        description={`¿Quitar "${site.name}" de Spanel? Solo elimina registro local. No borra containers remotos.`}
        confirmLabel="Eliminar de Spanel"
        destructive
        loading={busyAction === 'delete'}
        onClose={() => setDeleteOpen(false)}
        onConfirm={handleDelete}
      />
    </div>
  )
}
