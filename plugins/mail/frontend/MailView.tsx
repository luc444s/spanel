import { useEffect, useState } from 'react'
import { apiRequest } from '@systutor/shell'
import { Alert } from '@systutor/shell/ui/alert'
import { Badge } from '@systutor/shell/ui/badge'
import { Button } from '@systutor/shell/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@systutor/shell/ui/card'
import { ConfirmDialog } from '@systutor/shell/ui/confirm-dialog'
import { Dialog } from '@systutor/shell/ui/dialog'
import { Input } from '@systutor/shell/ui/input'
import { useAuthz } from '@spanel-app/authz'

type ServerStatus = { provisioned: boolean; status: string }
type MailDomain = { id: string; domain: string }
type Mailbox = {
  id: string
  email: string
  email_count: number
  size_mb: number
  quota_mb: number
}

function StorageBar({ used, quota }: { used: number; quota: number }) {
  const pct = quota > 0 ? Math.min((used / quota) * 100, 100) : 0
  const color = pct > 90 ? 'bg-destructive' : pct > 70 ? 'bg-yellow-500' : 'bg-primary'
  return (
    <div className="flex items-center gap-2 min-w-[160px]">
      <div className="flex-1 h-2 rounded-full bg-muted overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-muted-foreground whitespace-nowrap">
        {used} MB / {quota} MB
      </span>
    </div>
  )
}

export function MailView() {
  const { hasAnyPermission } = useAuthz()
  const [server, setServer] = useState<ServerStatus | null>(null)
  const [domains, setDomains] = useState<MailDomain[]>([])
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedDomain, setSelectedDomain] = useState<string | null>(null)

  const [provisioning, setProvisioning] = useState(false)

  const [showAddDomain, setShowAddDomain] = useState(false)
  const [addDomainValue, setAddDomainValue] = useState('')
  const [addDomainLoading, setAddDomainLoading] = useState(false)

  const [showCreateMailbox, setShowCreateMailbox] = useState(false)
  const [mbUser, setMbUser] = useState('')
  const [mbPassword, setMbPassword] = useState('')
  const [mbLoading, setMbLoading] = useState(false)
  const [createdMailbox, setCreatedMailbox] = useState<{ email: string; password: string } | null>(null)

  const [deleteDomain, setDeleteDomain] = useState<MailDomain | null>(null)
  const [deleteDomainLoading, setDeleteDomainLoading] = useState(false)

  const canServerRead = hasAnyPermission(['mail.server.read'])
  const canServerProvision = hasAnyPermission(['mail.server.provision'])
  const canDomainsRead = hasAnyPermission(['mail.domains.read'])
  const canDomainsCreate = hasAnyPermission(['mail.domains.create'])
  const canDomainsDelete = hasAnyPermission(['mail.domains.delete'])
  const canMailboxesRead = hasAnyPermission(['mail.mailboxes.read'])
  const canMailboxesCreate = hasAnyPermission(['mail.mailboxes.create'])

  const fetchAll = async () => {
    try {
      const [s, d, m] = await Promise.all([
        canServerRead ? apiRequest<ServerStatus>('/api/v1/plugins/mail/server/status') : Promise.resolve(null),
        canDomainsRead ? apiRequest<MailDomain[]>('/api/v1/plugins/mail/domains') : Promise.resolve([]),
        canMailboxesRead ? apiRequest<Mailbox[]>('/api/v1/plugins/mail/mailboxes') : Promise.resolve([]),
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

  useEffect(() => { void fetchAll() }, [canServerRead, canDomainsRead, canMailboxesRead])

  const filteredMailboxes = selectedDomain
    ? mailboxes.filter((m) => m.email.endsWith(`@${selectedDomain}`))
    : []

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
      if (selectedDomain === deleteDomain.domain) setSelectedDomain(null)
      setDeleteDomain(null)
      await fetchAll()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setDeleteDomainLoading(false)
    }
  }

  const handleCreateMailbox = async () => {
    if (!mbUser.trim() || !selectedDomain) return
    setMbLoading(true)
    try {
      const res = await apiRequest<{ email: string; password: string }>(
        '/api/v1/plugins/mail/mailboxes',
        {
          method: 'POST',
          body: JSON.stringify({
            domain: selectedDomain,
            user: mbUser.trim(),
            password: mbPassword.trim() || undefined,
          }),
        },
      )
      setShowCreateMailbox(false)
      setMbUser('')
      setMbPassword('')
      setCreatedMailbox(res)
      await fetchAll()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setMbLoading(false)
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

      {/* Header */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle>Mail</CardTitle>
            <CardDescription>docker-mailserver en el docker remoto</CardDescription>
          </div>
          <div className="flex items-center gap-3">
            <Badge className={server?.status === 'running' ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700' : undefined}>
              {server?.provisioned ? server.status : 'no provisionado'}
            </Badge>
            {!server?.provisioned && canServerProvision && (
              <Button onClick={handleProvision} disabled={provisioning} className="px-3 py-1.5 text-xs">
                {provisioning ? 'Provisionando…' : 'Provisionar'}
              </Button>
            )}
          </div>
        </CardHeader>
      </Card>

      {/* Two-panel layout */}
      <div className="grid grid-cols-1 md:grid-cols-[300px_1fr] gap-6">
        {/* Left panel: Domains */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-3">
            <CardTitle className="text-base">Dominios</CardTitle>
            {canDomainsCreate && <Button variant="secondary" className="px-3 py-1.5 text-xs" onClick={() => setShowAddDomain(true)}>+ Agregar</Button>}
          </CardHeader>
          <CardContent>
            {!canDomainsRead ? (
              <p className="text-sm text-muted-foreground">No autorizado para ver dominios.</p>
            ) : domains.length === 0 ? (
              <p className="text-sm text-muted-foreground">Sin dominios de correo.</p>
            ) : (
              <ul className="space-y-1">
                {domains.map((d) => {
                  const count = mailboxes.filter((m) => m.email.endsWith(`@${d.domain}`)).length
                  return (
                    <li
                      key={d.id}
                      className={`flex items-center justify-between rounded-md px-3 py-2 text-sm cursor-pointer transition-colors ${
                        selectedDomain === d.domain
                          ? 'bg-primary/10 text-primary font-medium'
                          : 'hover:bg-muted'
                      }`}
                      onClick={() => setSelectedDomain(d.domain)}
                    >
                      <div className="flex items-center gap-2 truncate">
                        <span className="truncate">{d.domain}</span>
                        <Badge className="text-xs shrink-0">{count}</Badge>
                      </div>
                      {canDomainsDelete && (
                        <Button
                          variant="secondary"
                          className="ml-2 h-6 w-6 shrink-0 border-0 bg-transparent p-0 text-muted-foreground hover:bg-transparent hover:text-destructive"
                          onClick={(e) => { e.stopPropagation(); setDeleteDomain(d) }}
                        >
                          ×
                        </Button>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* Right panel: Mailboxes */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-3">
            <div>
              <CardTitle className="text-base">
                {selectedDomain ? `Buzones de ${selectedDomain}` : 'Buzones'}
              </CardTitle>
              {selectedDomain && (
                <CardDescription>{filteredMailboxes.length} buzón(es)</CardDescription>
              )}
            </div>
            {selectedDomain && canMailboxesCreate && (
              <Button variant="secondary" className="px-3 py-1.5 text-xs" onClick={() => setShowCreateMailbox(true)}>
                + Crear buzón
              </Button>
            )}
          </CardHeader>
          <CardContent>
            {!canMailboxesRead ? (
              <p className="text-sm text-muted-foreground">No autorizado para ver buzones.</p>
            ) : !selectedDomain ? (
              <p className="text-sm text-muted-foreground">Seleccioná un dominio para ver sus buzones.</p>
            ) : filteredMailboxes.length === 0 ? (
              <p className="text-sm text-muted-foreground">Sin buzones en este dominio.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-muted-foreground">
                      <th className="py-2 pr-4 font-medium">Email</th>
                      <th className="py-2 pr-4 font-medium text-right">Correos</th>
                      <th className="py-2 font-medium">Almacenamiento</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredMailboxes.map((m) => (
                      <tr key={m.id} className="border-b border-border/50">
                        <td className="py-2 pr-4 font-medium">{m.email}</td>
                        <td className="py-2 pr-4 text-right text-muted-foreground">
                          {m.email_count}
                        </td>
                        <td className="py-2">
                          <StorageBar used={m.size_mb} quota={m.quota_mb} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

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
            <Button onClick={handleCreateMailbox} disabled={mbLoading || !mbUser.trim()}>
              {mbLoading ? 'Creando…' : 'Crear buzón'}
            </Button>
          </div>
        }
      >
        <div className="space-y-3">
          <div>
            <label className="text-sm font-medium">Dominio</label>
            <Input value={selectedDomain ?? ''} disabled />
          </div>
          <div>
            <label className="text-sm font-medium">Usuario</label>
            <Input
              placeholder="admin"
              value={mbUser}
              onChange={(e) => setMbUser(e.target.value)}
            />
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
    </div>
  )
}
