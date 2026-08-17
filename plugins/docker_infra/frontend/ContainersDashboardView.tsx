import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiRequest } from '@systutor/shell'
import { Alert } from '@systutor/shell/ui/alert'
import { Badge } from '@systutor/shell/ui/badge'
import { Button } from '@systutor/shell/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@systutor/shell/ui/card'

type ContainerRow = {
  name: string
  image: string
  state?: string
  status: string
}

type ContainerStats = {
  name: string
  cpu_percent: string | null
  mem_usage: string | null
  mem_percent: string | null
  net_io: string | null
  block_io: string | null
  pids: string | null
}

type Site = {
  id: string
  name: string
  container_name: string
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

export function ContainersDashboardView() {
  const [containers, setContainers] = useState<ContainerRow[]>([])
  const [statsByName, setStatsByName] = useState<Record<string, ContainerStats>>({})
  const [sitesByContainer, setSitesByContainer] = useState<Record<string, Site>>({})
  const [error, setError] = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  const fetchData = async (showLoading = false) => {
    if (showLoading) {
      setLoading(true)
    } else {
      setRefreshing(true)
    }
    setError(null)
    setWarning(null)

    try {
      const [containersResult, statsResult, sitesResult] = await Promise.allSettled([
        apiRequest<ContainerRow[]>('/api/v1/plugins/docker_infra/containers?all_containers=true'),
        apiRequest<ContainerStats[]>('/api/v1/plugins/docker_infra/containers/stats'),
        apiRequest<Site[]>('/api/v1/plugins/hosting/sites'),
      ])

      if (containersResult.status !== 'fulfilled') {
        throw containersResult.reason
      }

      setContainers(containersResult.value)

      if (statsResult.status === 'fulfilled') {
        setStatsByName(
          Object.fromEntries(statsResult.value.map((row) => [row.name, row])),
        )
      } else {
        setStatsByName({})
        setWarning('No se pudieron leer métricas live. Lista base disponible.')
      }

      if (sitesResult.status === 'fulfilled') {
        setSitesByContainer(
          Object.fromEntries(sitesResult.value.map((site) => [site.container_name, site])),
        )
      } else {
        setSitesByContainer({})
        setWarning((prev) => prev ?? 'No se pudieron enlazar Sites adoptados.')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => {
    void fetchData(true)
  }, [])

  const runningCount = containers.filter((container) => container.state === 'running').length
  const adoptedCount = containers.filter((container) => sitesByContainer[container.name]).length

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">Infra</h1>
          <p className="text-sm text-muted-foreground">Snapshot de containers remotos y recursos actuales</p>
        </div>
        <Button variant="secondary" onClick={() => void fetchData()} disabled={loading || refreshing}>
          {refreshing ? 'Actualizando...' : 'Actualizar'}
        </Button>
      </div>

      {error && <Alert title="Error">{error}</Alert>}
      {warning && <Alert title="Parcial">{warning}</Alert>}

      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader>
            <CardDescription>Total</CardDescription>
            <CardTitle>{containers.length}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader>
            <CardDescription>Running</CardDescription>
            <CardTitle>{runningCount}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader>
            <CardDescription>Stopped</CardDescription>
            <CardTitle>{containers.length - runningCount}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader>
            <CardDescription>Adoptados</CardDescription>
            <CardTitle>{adoptedCount}</CardTitle>
          </CardHeader>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Containers</CardTitle>
          <CardDescription>Read-only. Lifecycle se opera desde Hosting.</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="text-sm text-muted-foreground">Cargando…</p>
          ) : containers.length === 0 ? (
            <p className="text-sm text-muted-foreground">Sin containers remotos.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th className="py-2 pr-4 font-medium">Nombre</th>
                    <th className="py-2 pr-4 font-medium">Imagen</th>
                    <th className="py-2 pr-4 font-medium">Estado</th>
                    <th className="py-2 pr-4 font-medium">CPU</th>
                    <th className="py-2 pr-4 font-medium">Memoria</th>
                    <th className="py-2 pr-4 font-medium">Adoptado</th>
                    <th className="py-2 font-medium">PIDs</th>
                  </tr>
                </thead>
                <tbody>
                  {containers.map((container) => {
                    const state = container.state || container.status
                    const stats = statsByName[container.name]
                    const site = sitesByContainer[container.name]
                    const protectedContainer = container.name === 'orquestador_ardi_postgres'
                    return (
                      <tr key={container.name} className="border-b border-border/50">
                        <td className="py-2 pr-4">
                          <div className="flex items-center gap-2">
                            <span className="font-medium">{container.name}</span>
                            {protectedContainer && (
                              <Badge className="border-amber-500/30 bg-amber-500/10 text-amber-700">
                                protegido
                              </Badge>
                            )}
                          </div>
                        </td>
                        <td className="py-2 pr-4 text-muted-foreground">{container.image}</td>
                        <td className="py-2 pr-4">
                          <Badge className={statusBadgeClass(state)}>{state}</Badge>
                        </td>
                        <td className="py-2 pr-4 text-muted-foreground">{stats?.cpu_percent ?? '—'}</td>
                        <td className="py-2 pr-4 text-muted-foreground">
                          {stats?.mem_usage ?? '—'}
                          {stats?.mem_percent ? ` (${stats.mem_percent})` : ''}
                        </td>
                        <td className="py-2 pr-4">
                          {site ? (
                            <Link to={`/p/hosting/sites/${site.id}`} className="text-primary hover:underline">
                              {site.name}
                            </Link>
                          ) : (
                            <span className="text-muted-foreground">No</span>
                          )}
                        </td>
                        <td className="py-2 text-muted-foreground">{stats?.pids ?? '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
