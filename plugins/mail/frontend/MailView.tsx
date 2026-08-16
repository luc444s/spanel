import { useEffect, useState } from 'react'
import { apiRequest } from '@systutor/shell'
import { Alert } from '@systutor/shell/ui/alert'
import { Badge } from '@systutor/shell/ui/badge'
import { Button } from '@systutor/shell/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@systutor/shell/ui/card'
import { ConfirmDialog } from '@systutor/shell/ui/confirm-dialog'
import { Dialog } from '@systutor/shell/ui/dialog'
import { Input } from '@systutor/shell/ui/input'

type ServerStatus = { provisioned: boolean; status: string }
type MailDomain = { id: string; domain: string }
type Mailbox = { id: string; email: string }

export function MailView() {
  const [server, setServer] = useState<ServerStatus | null>(null)
  const [domains, setDomains] = useState<MailDomain[]>([])
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const [provisioning, setProvisioning] = useState(false)

  const [showAddDomain, setShowAddDomain] = useState(false)
  const [addDomainValue, setAddDomainValue] = useState('')
  const [addDomainLoading, setAddDomainLoading] = useState(false)

  const [showCreateMailbox, setShowCreateMailbox] = useState(false)
  const [mbUser, setMbUser] = useState('')
  const [mbDomain, setMbDomain] = useState('')
  const [mbPassword, setMbPassword] = useState('')
  const [mbLoading, setMbLoading] = useState(false)
  const [createdMailbox, setCreatedMailbox] = useState<{ email: string; password: string } | null>(null)

  const [deleteDomain, setDeleteDomain] = useState<MailDomain | null>(null)
  const [deleteDomainLoading, setDeleteDomainLoading] = useState(false)

  const [deleteMailbox, setDeleteMailbox] = useState<Mailbox | null>(null)
  const [deleteMailboxLoading, setDeleteMailboxLoading] = useState(false)

  const fetchAll = async () => {
    try {
      const [s, d, m] = await Promise.all([
        apiRequest<ServerStatus>('/api/v1/plugins/mail/server/status'),
        apiRequest<MailDomain[]>('/api/v1/plugins/mail/domains'),
        apiRequest<Mailbox[]>('/api/v1/plugins/mail/mailboxes'),
      ])
      setServer(s)
      setDomains(d)
      setMailboxes(m)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void fetchAll() }, [])

  const handleProvision = async () => {
    setProvisioning(true)
    try {
      await apiRequest('/api/v1/plugins/mail/server/ensure', { method: 'POST' })
      await fetchAll()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setProvisioning(false)
    }
  }

  const handleAddDomain = async () => {
    if (!addDomainValue.trim()) return
    setAddDomainLoading(true)
    try {
      await apiRequest('/api/v1/plugins/mail/domains', {
        method: 'POST',
        body: JSON.stringify({ domain: addDomainValue.trim() }),
      })
      setShowAddDomain(false)
      setAddDomainValue('')
      await fetchAll()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setAddDomainLoading(false)
    }
  }

  const handleDeleteDomain = async () => {
    if (!deleteDomain) return
    setDeleteDomainLoading(true)
    try {
      await apiRequest(`/api/v1/plugins/mail/domains/${deleteDomain.id}`, { method: 'DELETE' })
      setDeleteDomain(null)
      await fetchAll()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setDeleteDomainLoading(false)
    }
  }

  const handleCreateMailbox = async () => {
    if (!mbUser.trim() || !mbDomain) return
    setMbLoading(true)
    try {
      const res = await apiRequest<{ email: string; password: string }>(
        '/api/v1/plugins/mail/mailboxes',
        {
          method: 'POST',
          body: JSON.stringify({
            domain: mbDomain,
            user: mbUser.trim(),
            password: mbPassword.trim() || undefined,
          }),
        },
      )
      setShowCreateMailbox(false)
      setMbUser('')
      setMbDomain('')
      setMbPassword('')
      setCreatedMailbox(res)
      await fetchAll()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setMbLoading(false)
    }
  }

  const handleDeleteMailbox = async () => {
    if (!deleteMailbox) return
    setDeleteMailboxLoading(true)
    try {
      await apiRequest(`/api/v1/plugins/mail/mailboxes/${deleteMailbox.id}`, { method: 'DELETE' })
      setDeleteMailbox(null)
      await fetchAll()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setDeleteMailboxLoading(false)
    }
  }

  if (loading) {
    return (
      <Card>
        <CardContent className="py-8">
          <p className="text-sm text-muted-foreground">Cargando…</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      {error && <Alert title="Error">{error}</Alert>}

      {/* Server status */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle>Mail</CardTitle>
            <CardDescription>docker-mailserver en el docker remoto</CardDescription>
          </div>
          {!server?.provisioned && (
            <Button onClick={handleProvision} disabled={provisioning}>
              {provisioning ? 'Provisionando…' : 'Provisionar'}
            </Button>
          )}
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2">
            <span className="text-sm">Estado:</span>
            <Badge variant={server?.status === 'running' ? 'default' : 'secondary'}>
              {server?.provisioned ? server.status : 'no provisionado'}
            </Badge>
          </div>
        </CardContent>
      </Card>

      {/* Domains */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle>Dominios de correo</CardTitle>
            <CardDescription>Dominios habilitados para recibir correo</CardDescription>
          </div>
          <Button variant="secondary" onClick={() => setShowAddDomain(true)}>+ Agregar</Button>
        </CardHeader>
        <CardContent>
          {domains.length === 0 ? (
            <p className="text-sm text-muted-foreground">Sin dominios de correo.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th className="py-2 pr-4 font-medium">Dominio</th>
                    <th className="py-2 font-medium">Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {domains.map((d) => (
                    <tr key={d.id} className="border-b border-border/50">
                      <td className="py-2 pr-4 font-medium">{d.domain}</td>
                      <td className="py-2">
                        <Button
                          variant="secondary"
                          size="sm"
                          className="text-destructive"
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
      </Card>

      {/* Mailboxes */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle>Buzones</CardTitle>
            <CardDescription>Cuentas de correo configuradas</CardDescription>
          </div>
          <Button variant="secondary" onClick={() => { setMbDomain(domains[0]?.domain ?? ''); setShowCreateMailbox(true) }}>
            + Crear buzón
          </Button>
        </CardHeader>
        <CardContent>
          {mailboxes.length === 0 ? (
            <p className="text-sm text-muted-foreground">Sin buzones.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th className="py-2 pr-4 font-medium">Email</th>
                    <th className="py-2 font-medium">Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {mailboxes.map((m) => (
                    <tr key={m.id} className="border-b border-border/50">
                      <td className="py-2 pr-4 font-medium">{m.email}</td>
                      <td className="py-2">
                        <Button
                          variant="secondary"
                          size="sm"
                          className="text-destructive"
                          onClick={() => setDeleteMailbox(m)}
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
      </Card>

      {/* Add domain dialog */}
      <Dialog
        open={showAddDomain}
        title="Agregar dominio de correo"
        onClose={() => setShowAddDomain(false)}
        maxWidthClassName="max-w-md"
        actions={
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setShowAddDomain(false)}>Cancelar</Button>
            <Button onClick={handleAddDomain} disabled={addDomainLoading || !addDomainValue.trim()}>
              {addDomainLoading ? 'Creando…' : 'Crear'}
            </Button>
          </div>
        }
      >
        <div>
          <label className="text-sm font-medium">Dominio</label>
          <Input
            placeholder="midominio.com"
            value={addDomainValue}
            onChange={(e) => setAddDomainValue(e.target.value)}
          />
        </div>
      </Dialog>

      {/* Create mailbox dialog */}
      <Dialog
        open={showCreateMailbox}
        title="Crear buzón de correo"
        onClose={() => setShowCreateMailbox(false)}
        maxWidthClassName="max-w-md"
        actions={
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setShowCreateMailbox(false)}>Cancelar</Button>
            <Button onClick={handleCreateMailbox} disabled={mbLoading || !mbUser.trim() || !mbDomain}>
              {mbLoading ? 'Creando…' : 'Crear buzón'}
            </Button>
          </div>
        }
      >
        <div className="space-y-3">
          <div>
            <label className="text-sm font-medium">Usuario</label>
            <Input
              placeholder="admin"
              value={mbUser}
              onChange={(e) => setMbUser(e.target.value)}
            />
          </div>
          <div>
            <label className="text-sm font-medium">Dominio</label>
            <select
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={mbDomain}
              onChange={(e) => setMbDomain(e.target.value)}
            >
              {domains.length === 0 && <option value="">Sin dominios</option>}
              {domains.map((d) => (
                <option key={d.id} value={d.domain}>{d.domain}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-sm font-medium">Contraseña (auto si vacío)</label>
            <Input
              type="password"
              placeholder="Se genera automáticamente"
              value={mbPassword}
              onChange={(e) => setMbPassword(e.target.value)}
            />
          </div>
        </div>
      </Dialog>

      {/* Created mailbox credential dialog */}
      <Dialog
        open={!!createdMailbox}
        title="¡Buzón creado!"
        onClose={() => setCreatedMailbox(null)}
        maxWidthClassName="max-w-md"
        actions={
          <div className="flex justify-end">
            <Button onClick={() => setCreatedMailbox(null)}>Cerrar</Button>
          </div>
        }
      >
        {createdMailbox && (
          <div className="space-y-3">
            <div className="text-sm">
              <span className="text-muted-foreground">Email:</span>{' '}
              <span className="font-mono font-medium">{createdMailbox.email}</span>
            </div>
            <div className="text-sm">
              <span className="text-muted-foreground">Contraseña:</span>{' '}
              <span className="font-mono font-medium">{createdMailbox.password}</span>
            </div>
            <Alert title="Importante">
              Copiá la contraseña ahora. No se mostrará de nuevo.
            </Alert>
          </div>
        )}
      </Dialog>

      {/* Delete domain confirmation */}
      <ConfirmDialog
        open={!!deleteDomain}
        title="Eliminar dominio de correo"
        description={`¿Eliminar "${deleteDomain?.domain}"? No se podrá recibir correo en este dominio.`}
        confirmLabel="Eliminar"
        destructive
        loading={deleteDomainLoading}
        onClose={() => setDeleteDomain(null)}
        onConfirm={handleDeleteDomain}
      />

      {/* Delete mailbox confirmation */}
      <ConfirmDialog
        open={!!deleteMailbox}
        title="Eliminar buzón"
        description={`¿Eliminar "${deleteMailbox?.email}"? Se perderán los correos de este buzón.`}
        confirmLabel="Eliminar"
        destructive
        loading={deleteMailboxLoading}
        onClose={() => setDeleteMailbox(null)}
        onConfirm={handleDeleteMailbox}
      />
    </div>
  )
}
