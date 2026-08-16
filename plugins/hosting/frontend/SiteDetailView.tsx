import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiRequest } from '@systutor/shell'
import { Alert } from '@systutor/shell/ui/alert'
import { Badge } from '@systutor/shell/ui/badge'
import { Button } from '@systutor/shell/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@systutor/shell/ui/card'
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

export function SiteDetailView() {
  const { id } = useParams<{ id: string }>()
  const [site, setSite] = useState<SiteDetail | null>(null)
  const [logs, setLogs] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    void (async () => {
      try {
        setSite(await apiRequest<SiteDetail>(`/api/v1/plugins/hosting/sites/${id}`))
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    })()
  }, [id])

  const loadLogs = async () => {
    if (!id) return
    setError(null)
    try {
      const res = await apiRequest<{ lines: string }>(
        `/api/v1/plugins/hosting/sites/${id}/logs?tail=50`,
      )
      setLogs(res.lines)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  if (error) {
    return (
      <div className="space-y-4">
        <Alert title="Error">{error}</Alert>
        <Link to="/p/hosting/sites" className="text-sm text-primary hover:underline">
          ← Volver a sitios
        </Link>
      </div>
    )
  }

  if (!site) {
    return <p className="text-sm text-muted-foreground">Cargando…</p>
  }

  const { origin } = site

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/p/hosting/sites" className="text-sm text-primary hover:underline">
            ← Sitios
          </Link>
          <h1 className="text-lg font-semibold">{site.name}</h1>
          <Badge>{site.stack}</Badge>
          <Badge>{origin.container_status}</Badge>
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

        <Card>
          <CardHeader>
            <CardTitle>Logs</CardTitle>
            <CardDescription>Últimas 50 líneas del container</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Button variant="secondary" onClick={() => void loadLogs()}>
              Cargar logs
            </Button>
            {logs !== null && (
              <pre className="max-h-72 overflow-auto rounded-md border border-border bg-secondary p-3 text-xs">
                {logs || '(sin salida)'}
              </pre>
            )}
          </CardContent>
        </Card>
      </div>

      {site.stack === 'wordpress' && site.domains.length > 0 && (
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
    </div>
  )
}
