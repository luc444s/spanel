import { useEffect, useState } from 'react'
import { apiRequest } from '@systutor/shell'
import { Alert } from '@systutor/shell/ui/alert'
import { Badge } from '@systutor/shell/ui/badge'
import { Button } from '@systutor/shell/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@systutor/shell/ui/card'
import { ConfirmDialog } from '@systutor/shell/ui/confirm-dialog'
import { Dialog } from '@systutor/shell/ui/dialog'
import { Input } from '@systutor/shell/ui/input'

type Domain = {
  id: string
  site_id: string
  fqdn: string
  ssl_status: string
}

export function DomainsView() {
  const [domains, setDomains] = useState<Domain[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const [showAdd, setShowAdd] = useState(false)
  const [addFqdn, setAddFqdn] = useState('')
  const [addSiteId, setAddSiteId] = useState('')
  const [addLoading, setAddLoading] = useState(false)

  const [editDomain, setEditDomain] = useState<Domain | null>(null)
  const [editFqdn, setEditFqdn] = useState('')
  const [editLoading, setEditLoading] = useState(false)

  const [deleteDomain, setDeleteDomain] = useState<Domain | null>(null)
  const [deleteLoading, setDeleteLoading] = useState(false)

  const fetchDomains = async () => {
    try {
      setDomains(await apiRequest<Domain[]>('/api/v1/plugins/proxy/domains'))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void fetchDomains() }, [])

  const handleAdd = async () => {
    if (!addFqdn.trim()) return
    setAddLoading(true)
    try {
      const body: Record<string, string> = { fqdn: addFqdn.trim() }
      if (addSiteId.trim()) body.site_id = addSiteId.trim()
      await apiRequest('/api/v1/plugins/proxy/domains', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      setShowAdd(false)
      setAddFqdn('')
      setAddSiteId('')
      await fetchDomains()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setAddLoading(false)
    }
  }

  const handleEdit = async () => {
    if (!editDomain || !editFqdn.trim()) return
    setEditLoading(true)
    try {
      await apiRequest(`/api/v1/plugins/proxy/domains/${editDomain.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ fqdn: editFqdn.trim() }),
      })
      setEditDomain(null)
      await fetchDomains()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setEditLoading(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteDomain) return
    setDeleteLoading(true)
    try {
      await apiRequest(`/api/v1/plugins/proxy/domains/${deleteDomain.id}`, {
        method: 'DELETE',
      })
      setDeleteDomain(null)
      await fetchDomains()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setDeleteLoading(false)
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <div>
          <CardTitle>Dominios</CardTitle>
          <CardDescription>Gestiona los dominios y su ruteo en Traefik</CardDescription>
        </div>
        <Button onClick={() => setShowAdd(true)}>Agregar dominio</Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <Alert title="Error">{error}</Alert>}
        {loading ? (
          <p className="text-sm text-muted-foreground">Cargando…</p>
        ) : domains.length === 0 ? (
          <p className="text-sm text-muted-foreground">Sin dominios configurados.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="py-2 pr-4 font-medium">FQDN</th>
                  <th className="py-2 pr-4 font-medium">Site ID</th>
                  <th className="py-2 pr-4 font-medium">SSL</th>
                  <th className="py-2 font-medium">Acciones</th>
                </tr>
              </thead>
              <tbody>
                {domains.map((d) => (
                  <tr key={d.id} className="border-b border-border/50">
                    <td className="py-2 pr-4 font-medium">{d.fqdn}</td>
                    <td className="py-2 pr-4 text-muted-foreground font-mono text-xs">{d.site_id.slice(0, 8)}</td>
                    <td className="py-2 pr-4">
                      <Badge className={d.ssl_status === 'active' ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700' : undefined}>
                        {d.ssl_status}
                      </Badge>
                    </td>
                    <td className="py-2 flex gap-2">
                      <Button
                        variant="secondary"
                        className="px-3 py-1.5 text-xs"
                        onClick={() => { setEditDomain(d); setEditFqdn(d.fqdn) }}
                      >
                        Editar
                      </Button>
                      <Button
                        variant="secondary"
                        className="px-3 py-1.5 text-xs text-destructive"
                        onClick={() => setDeleteDomain(d)}
                      >
                        Eliminar
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>

      {/* Add dialog */}
      <Dialog
        open={showAdd}
        title="Agregar dominio"
        onClose={() => setShowAdd(false)}
        maxWidthClassName="max-w-md"
        actions={
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setShowAdd(false)}>Cancelar</Button>
            <Button onClick={handleAdd} disabled={addLoading || !addFqdn.trim()}>
              {addLoading ? 'Creando...' : 'Crear'}
            </Button>
          </div>
        }
      >
        <div className="space-y-3">
          <div>
            <label className="text-sm font-medium">FQDN</label>
            <Input
              placeholder="mi-dominio.com"
              value={addFqdn}
              onChange={(e) => setAddFqdn(e.target.value)}
            />
          </div>
          <div>
            <label className="text-sm font-medium">Site ID (opcional)</label>
            <Input
              placeholder="Se infiere del nombre si se omite"
              value={addSiteId}
              onChange={(e) => setAddSiteId(e.target.value)}
            />
          </div>
        </div>
      </Dialog>

      {/* Edit dialog */}
      <Dialog
        open={!!editDomain}
        title="Editar dominio"
        onClose={() => setEditDomain(null)}
        maxWidthClassName="max-w-md"
        actions={
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setEditDomain(null)}>Cancelar</Button>
            <Button onClick={handleEdit} disabled={editLoading || !editFqdn.trim()}>
              {editLoading ? 'Guardando...' : 'Guardar'}
            </Button>
          </div>
        }
      >
        <div>
          <label className="text-sm font-medium">FQDN</label>
          <Input
            value={editFqdn}
            onChange={(e) => setEditFqdn(e.target.value)}
          />
        </div>
      </Dialog>

      {/* Delete confirmation */}
      <ConfirmDialog
        open={!!deleteDomain}
        title="Eliminar dominio"
        description={`¿Eliminar "${deleteDomain?.fqdn}"? Traefik dejará de rutear este dominio.`}
        confirmLabel="Eliminar"
        destructive
        loading={deleteLoading}
        onClose={() => setDeleteDomain(null)}
        onConfirm={handleDelete}
      />
    </Card>
  )
}
