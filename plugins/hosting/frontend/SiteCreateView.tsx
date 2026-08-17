import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { apiRequest } from '@systutor/shell'
import { Alert } from '@systutor/shell/ui/alert'
import { Badge } from '@systutor/shell/ui/badge'
import { Button } from '@systutor/shell/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@systutor/shell/ui/card'
import { Dialog } from '@systutor/shell/ui/dialog'
import { Input } from '@systutor/shell/ui/input'
import type { Site } from './SitesView'

type RemoteContainer = {
  name: string
  image: string
  state?: string
  status: string
}

type ProvisionResponse = {
  id: string
  name: string
  admin_user: string
  admin_password: string
  domains: string[]
}

function inferStack(image: string) {
  const lower = image.toLowerCase()
  if (lower.includes('wordpress')) return 'wordpress'
  if (lower.includes('mariadb') || lower.includes('mysql') || lower.includes('postgres')) return 'db'
  if (lower.includes('php')) return 'php'
  return 'static'
}

function statusBadgeClass(status: string) {
  if (status === 'running') return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700'
  if (status === 'exited' || status === 'stopped') {
    return 'border-border bg-secondary text-secondary-foreground'
  }
  return 'border-border bg-secondary text-secondary-foreground'
}

export function SiteCreateView() {
  const navigate = useNavigate()
  const [mode, setMode] = useState<'adopt' | 'provision'>('adopt')
  const [containers, setContainers] = useState<RemoteContainer[]>([])
  const [sites, setSites] = useState<Site[]>([])
  const [selectedContainer, setSelectedContainer] = useState<RemoteContainer | null>(null)
  const [adoptName, setAdoptName] = useState('')
  const [wpName, setWpName] = useState('')
  const [adminEmail, setAdminEmail] = useState('')
  const [adminUser, setAdminUser] = useState('')
  const [domain, setDomain] = useState('')
  const [createdSite, setCreatedSite] = useState<ProvisionResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    void (async () => {
      try {
        const [remoteContainers, existingSites] = await Promise.all([
          apiRequest<RemoteContainer[]>('/api/v1/plugins/docker_infra/containers?all_containers=true'),
          apiRequest<Site[]>('/api/v1/plugins/hosting/sites'),
        ])
        setContainers(remoteContainers)
        setSites(existingSites)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  const adoptedNames = new Set(sites.map((site) => site.container_name))
  const adoptableContainers = containers.filter((container) => !adoptedNames.has(container.name))

  const handleAdopt = async () => {
    if (!selectedContainer) return
    setSubmitting(true)
    setError(null)
    try {
      const created = await apiRequest<{ id: string }>(`/api/v1/plugins/hosting/sites/adopt`, {
        method: 'POST',
        body: JSON.stringify({
          container_name: selectedContainer.name,
          name: adoptName.trim() || undefined,
        }),
      })
      navigate(`/p/hosting/sites/${created.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  const handleProvision = async () => {
    if (!wpName.trim() || !adminEmail.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      const created = await apiRequest<ProvisionResponse>(
        '/api/v1/plugins/hosting/sites/provision/wordpress',
        {
          method: 'POST',
          body: JSON.stringify({
            name: wpName.trim(),
            admin_email: adminEmail.trim(),
            admin_user: adminUser.trim() || undefined,
            domain: domain.trim() || undefined,
          }),
        },
      )
      setCreatedSite(created)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link to="/p/hosting/sites" className="text-sm text-primary hover:underline">
          ← Sitios
        </Link>
        <h1 className="text-lg font-semibold">Nuevo sitio</h1>
      </div>

      {error && <Alert title="Error">{error}</Alert>}

      <div className="flex flex-wrap gap-2">
        <Button variant={mode === 'adopt' ? 'primary' : 'secondary'} onClick={() => setMode('adopt')}>
          Adoptar container
        </Button>
        <Button variant={mode === 'provision' ? 'primary' : 'secondary'} onClick={() => setMode('provision')}>
          Provisionar WordPress
        </Button>
      </div>

      {loading ? (
        <Card>
          <CardContent className="py-8 text-sm text-muted-foreground">Cargando…</CardContent>
        </Card>
      ) : mode === 'adopt' ? (
        <div className="grid gap-6 lg:grid-cols-[1.4fr_0.9fr]">
          <Card>
            <CardHeader>
              <CardTitle>Containers remotos</CardTitle>
              <CardDescription>Solo se muestran containers no adoptados todavía</CardDescription>
            </CardHeader>
            <CardContent>
              {adoptableContainers.length === 0 ? (
                <p className="text-sm text-muted-foreground">No hay containers disponibles para adoptar.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border text-left text-muted-foreground">
                        <th className="py-2 pr-4 font-medium">Nombre</th>
                        <th className="py-2 pr-4 font-medium">Imagen</th>
                        <th className="py-2 pr-4 font-medium">Estado</th>
                        <th className="py-2 pr-4 font-medium">Stack</th>
                        <th className="py-2 font-medium">Acción</th>
                      </tr>
                    </thead>
                    <tbody>
                      {adoptableContainers.map((container) => {
                        const isProtected = container.name === 'orquestador_ardi_postgres'
                        return (
                          <tr key={container.name} className="border-b border-border/50">
                            <td className="py-2 pr-4 font-medium">{container.name}</td>
                            <td className="py-2 pr-4 text-muted-foreground">{container.image}</td>
                            <td className="py-2 pr-4">
                              <Badge className={statusBadgeClass(container.state || container.status)}>
                                {container.state || container.status}
                              </Badge>
                            </td>
                            <td className="py-2 pr-4">
                              <Badge>{inferStack(container.image)}</Badge>
                            </td>
                            <td className="py-2">
                              {isProtected ? (
                                <Badge className="border-amber-500/30 bg-amber-500/10 text-amber-700">
                                  protegido
                                </Badge>
                              ) : (
                                <Button variant="secondary" onClick={() => setSelectedContainer(container)}>
                                  Seleccionar
                                </Button>
                              )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Adoptar</CardTitle>
              <CardDescription>Registra container existente dentro de Spanel sin tocar Docker remoto</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <div className="text-sm font-medium">Container seleccionado</div>
                <div className="mt-1 text-sm text-muted-foreground">
                  {selectedContainer ? selectedContainer.name : 'Elegí un container de la tabla'}
                </div>
              </div>
              <div>
                <label className="text-sm font-medium">Nombre visible (opcional)</label>
                <Input
                  placeholder={selectedContainer?.name ?? 'mi-sitio'}
                  value={adoptName}
                  onChange={(e) => setAdoptName(e.target.value)}
                />
              </div>
              <Button onClick={() => void handleAdopt()} disabled={submitting || !selectedContainer}>
                {submitting && mode === 'adopt' ? 'Adoptando...' : 'Adoptar container'}
              </Button>
            </CardContent>
          </Card>
        </div>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Provisionar WordPress</CardTitle>
            <CardDescription>Crea stack WordPress en Docker remoto y registra sitio en Spanel</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <div>
              <label className="text-sm font-medium">Nombre</label>
              <Input placeholder="demowp" value={wpName} onChange={(e) => setWpName(e.target.value)} />
            </div>
            <div>
              <label className="text-sm font-medium">Admin email</label>
              <Input
                placeholder="admin@midominio.com"
                value={adminEmail}
                onChange={(e) => setAdminEmail(e.target.value)}
              />
            </div>
            <div>
              <label className="text-sm font-medium">Admin user (opcional)</label>
              <Input placeholder="admin" value={adminUser} onChange={(e) => setAdminUser(e.target.value)} />
            </div>
            <div>
              <label className="text-sm font-medium">Dominio (opcional)</label>
              <Input placeholder="blog.midominio.com" value={domain} onChange={(e) => setDomain(e.target.value)} />
              <p className="mt-1 text-xs text-muted-foreground">
                Si se carga, también configura siteurl/home vía flujo dual actual.
              </p>
            </div>
            <div className="md:col-span-2">
              <Button onClick={() => void handleProvision()} disabled={submitting || !wpName.trim() || !adminEmail.trim()}>
                {submitting && mode === 'provision' ? 'Provisionando...' : 'Provisionar WordPress'}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Dialog
        open={!!createdSite}
        title="Credenciales WordPress"
        description="Guardá estas credenciales ahora. Spanel no las vuelve a pedir al backend."
        onClose={() => setCreatedSite(null)}
        maxWidthClassName="max-w-lg"
        actions={
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setCreatedSite(null)}>
              Cerrar
            </Button>
            <Button
              onClick={() => {
                if (!createdSite) return
                navigate(`/p/hosting/sites/${createdSite.id}`)
              }}
            >
              Ir al sitio
            </Button>
          </div>
        }
      >
        {createdSite && (
          <div className="space-y-3 text-sm">
            <div>
              <div className="text-muted-foreground">Usuario admin</div>
              <div className="font-mono">{createdSite.admin_user}</div>
            </div>
            <div>
              <div className="text-muted-foreground">Contraseña admin</div>
              <div className="font-mono break-all">{createdSite.admin_password}</div>
            </div>
            <div>
              <div className="text-muted-foreground">Site</div>
              <div>{createdSite.name}</div>
            </div>
            <div>
              <div className="text-muted-foreground">Dominios</div>
              <div>{createdSite.domains.length ? createdSite.domains.join(', ') : 'Sin dominio inicial'}</div>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  )
}
