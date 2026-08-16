# A.SPEC SP-0013 — Mail: adopt or provision docker-mailserver

## WHY

Docker mail es la segunda pata del producto. Los sitios wp necesitan
SMTP saliente; el tenant necesita dominios, buzones y aliases. Elección:
docker-mailserver (arquitectura-base.md §8, decisión 2).

## WHAT

Una transición observable: admin adopta un docker-mailserver existente o
provisiona uno nuevo; luego crea dominios de mail, buzones y aliases, y
Spanel emite `mail.smtp_provisioned` para que los sitios consuman SMTP.

## SCOPE

- Plugin `mail`: adoptar container mail existente (config por env) o
  provisionar docker-mailserver (1 container, volumen de config).
- Tablas `mail_domain`, `mailbox`, `alias` (migraciones del plugin).
- `POST /api/v1/plugins/mail/domains`, `/mailboxes`, `/aliases` — aplican
  config al server (setup.sh / env) y recargan.
- DKIM/SPF: generación de registros a publicar (el DNS externo se apunta
  manual, como SP-0011).
- Evento `mail.smtp_provisioned` {tenant, smtp_host, credenciales} para
  que hosting lo consuma (wp smtp).

## OUT OF SCOPE

- Webmail (roundcube/snappymail) — spec futura.
- Migración de buzones, cuotas, políticas anti-spam finas.
- Gestión DNS automática.

## CONTRACT

- PRE: docker remoto accesible; puerto 25/465/587 expuesto solo en
  tailscale/red interna.
- POST: dominio mail operativo (envía/recibe o al menos SMTP saliente
  verificado); buzones creados usables; aliases resueltos; evento
  emitido una vez por tenant al activar SMTP.

## INVARIANTS

```yaml
invariants:
  - Credenciales de buzones MUST NO loguearse en claro.
  - Mail de tenants ajenos MUST NO tocarse.
  - Kernel MUST NOT ser modificado.
  - Puerto 25 abierto SOLO en red tailscale/interna, nunca público.
```

## VERIFICATION

```bash
TOKEN=$(...login...)
curl -s -X POST http://127.0.0.1:8001/api/v1/plugins/mail/domains -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"domain":"test.<ts>.ts.net"}'
# SMTP: swaks o telnet contra el mailserver via tailscale
```

## ROLLBACK

Config revertida (quitar dominio/buzón del setup); container
provisionado se borra solo si fue creado por esta spec.

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/mail/**
  prohibited:
    - vendor/**
    - apps/web/**
    - plugins/hosting/**
    - plugins/docker-infra/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - mail.server
  indirect:
    - hosting.smtp (via evento)
  must_not_affect:
    - kernel.auth
    - containers no adoptados
    - mail externo existente
```

## Traceability

- Requirement: arquitectura-base.md §8.
- Commit: root (pendiente)
- Deployment: `npm run services:no-reload`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
