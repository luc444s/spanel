import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiRequest } from '@systutor/shell'
import { Alert } from '@systutor/shell/ui/alert'
import { Badge } from '@systutor/shell/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@systutor/shell/ui/card'

export type Site = {
  id: string
  tenant_id: string
  branch_id: string | null
  stack: string
  name: string
  container_name: string
  domains: string[]
  created_at: string | null
}

export function SitesView() {
  const [sites, setSites] = useState<Site[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    void (async () => {
      try {
        setSites(await apiRequest<Site[]>('/api/v1/plugins/hosting/sites'))
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sitios</CardTitle>
        <CardDescription>Containers adoptados del docker remoto</CardDescription>
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
                  <th className="py-2 pr-4 font-medium">Container</th>
                  <th className="py-2 font-medium">Dominios</th>
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
                    <td className="py-2 pr-4 text-muted-foreground">{site.container_name}</td>
                    <td className="py-2 text-muted-foreground">
                      {site.domains.length ? site.domains.join(', ') : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
