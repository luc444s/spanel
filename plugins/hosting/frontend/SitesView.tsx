import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { apiRequest } from '@systutor/shell'
import { Alert } from '@systutor/shell/ui/alert'
import { Badge } from '@systutor/shell/ui/badge'
import { Button } from '@systutor/shell/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@systutor/shell/ui/card'
import { ConfirmDialog } from '@systutor/shell/ui/confirm-dialog'

export type Site = {
  id: string
  tenant_id: string
  branch_id: string | null
  stack: string
  name: string
  container_name: string
  domains: string[]
  created_at: string | null
  container_status: string
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

export function SitesView() {
  const navigate = useNavigate()
  const [sites, setSites] = useState<Site[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [busySiteId, setBusySiteId] = useState<string | null>(null)
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [deleteSite, setDeleteSite] = useState<Site | null>(null)

  const fetchSites = async (showLoading = false) => {
    if (showLoading) {
      setLoading(true)
    } else {
      setRefreshing(true)
    }

    try {
      setError(null)
      setSites(await apiRequest<Site[]>('/api/v1/plugins/hosting/sites'))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => {
    void fetchSites(true)
  }, [])

  const runLifecycle = async (siteId: string, action: 'start' | 'stop' | 'restart') => {
    setBusySiteId(siteId)
    setBusyAction(action)
    setError(null)
    try {
      await apiRequest(`/api/v1/plugins/hosting/sites/${siteId}/${action}`, { method: 'POST' })
      await fetchSites()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusySiteId(null)
      setBusyAction(null)
    }
  }

  const handleDelete = async () => {
    if (!deleteSite) return
    setBusySiteId(deleteSite.id)
    setBusyAction('delete')
    setError(null)
    try {
      await apiRequest(`/api/v1/plugins/hosting/sites/${deleteSite.id}`, { method: 'DELETE' })
      setDeleteSite(null)
      await fetchSites()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusySiteId(null)
      setBusyAction(null)
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4">
        <div>
          <CardTitle>Sitios</CardTitle>
          <CardDescription>Containers adoptados y stacks provisionados en docker remoto</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => void fetchSites()} disabled={refreshing || loading}>
            {refreshing ? 'Actualizando...' : 'Actualizar'}
          </Button>
          <Button onClick={() => navigate('/p/hosting/sites/new')}>Nuevo sitio</Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <Alert title="Error">{error}</Alert>}
        {loading ? (
          <p className="text-sm text-muted-foreground">Cargando…</p>
        ) : sites.length === 0 ? (
          <p className="text-sm text-muted-foreground">Sin sitios adoptados.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="py-2 pr-4 font-medium">Nombre</th>
                  <th className="py-2 pr-4 font-medium">Stack</th>
                  <th className="py-2 pr-4 font-medium">Estado</th>
                  <th className="py-2 pr-4 font-medium">Container</th>
                  <th className="py-2 pr-4 font-medium">Dominios</th>
                  <th className="py-2 font-medium">Acciones</th>
                </tr>
              </thead>
              <tbody>
                {sites.map((site) => (
                  <tr key={site.id} className="border-b border-border/50">
                    <td className="py-2 pr-4">
                      <Link
                        to={`/p/hosting/sites/${site.id}`}
                        className="font-medium text-primary hover:underline"
                      >
                        {site.name}
                      </Link>
                    </td>
                    <td className="py-2 pr-4">
                      <Badge>{site.stack}</Badge>
                    </td>
                    <td className="py-2 pr-4">
                      <Badge className={statusBadgeClass(site.container_status)}>
                        {site.container_status}
                      </Badge>
                    </td>
                    <td className="py-2 pr-4 text-muted-foreground">{site.container_name}</td>
                    <td className="py-2 pr-4 text-muted-foreground">
                      {site.domains.length ? site.domains.join(', ') : '—'}
                    </td>
                    <td className="py-2">
                      <div className="flex flex-wrap gap-2">
                        <Button
                          variant="secondary"
                          onClick={() => void runLifecycle(site.id, 'start')}
                          disabled={busySiteId === site.id}
                        >
                          {busySiteId === site.id && busyAction === 'start' ? 'Iniciando...' : 'Start'}
                        </Button>
                        <Button
                          variant="secondary"
                          onClick={() => void runLifecycle(site.id, 'stop')}
                          disabled={busySiteId === site.id}
                        >
                          {busySiteId === site.id && busyAction === 'stop' ? 'Deteniendo...' : 'Stop'}
                        </Button>
                        <Button
                          variant="secondary"
                          onClick={() => void runLifecycle(site.id, 'restart')}
                          disabled={busySiteId === site.id}
                        >
                          {busySiteId === site.id && busyAction === 'restart' ? 'Reiniciando...' : 'Restart'}
                        </Button>
                        <Button variant="secondary" onClick={() => navigate(`/p/hosting/sites/${site.id}`)}>
                          Detalle
                        </Button>
                        <Button
                          variant="secondary"
                          className="text-destructive"
                          onClick={() => setDeleteSite(site)}
                          disabled={busySiteId === site.id}
                        >
                          Eliminar
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>

      <ConfirmDialog
        open={!!deleteSite}
        title="Eliminar sitio de Spanel"
        description={deleteSite
          ? `¿Quitar "${deleteSite.name}" de Spanel? Solo elimina registro local. No borra containers remotos.`
          : undefined}
        confirmLabel="Eliminar de Spanel"
        destructive
        loading={busySiteId === deleteSite?.id && busyAction === 'delete'}
        onClose={() => setDeleteSite(null)}
        onConfirm={handleDelete}
      />
    </Card>
  )
}
