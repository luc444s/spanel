# A.SPEC SP-0022 — Mail frontend

## WHY

Plugin mail tiene backend completo (provision server, domains, mailboxes)
pero frontend vacío. Sin UI, el admin no puede gestionar correo sin curl.
Mismo patrón que SP-0021 (proxy domains CRUD).

## WHAT

Transición observable: admin gestiona mail server, dominios y buzones
desde la UI. Puede provisionar el mail server, agregar/eliminar dominios
de correo y crear/eliminar buzones. La contraseña se muestra una sola
vez al crear el buzón.

## SCOPE

### Backend (`plugins/mail/backend/plugin.py`)

- `GET /domains` — listar dominios mail del tenant.
- `DELETE /domains/{id}` — eliminar dominio mail de DB.
- `DELETE /mailboxes/{id}` — eliminar buzón de DB + `setup email del`
  en el container de mail.
- Endpoints existentes sin cambios: `POST /domains`, `POST /mailboxes`,
  `GET /mailboxes`, `GET /server/status`, `POST /server/ensure`.

### Frontend (`plugins/mail/frontend/`)

- `MailView.tsx` — componente principal con 3 secciones:
  1. Server status — badge estado + botón "Provisionar" si no existe.
  2. Dominios — tabla + botón agregar + eliminar.
  3. Buzones — tabla + botón crear + eliminar. Modal post-creación
     muestra email + contraseña una sola vez.
- `register.tsx` — route `/mail` + nav item "Mail".
- `plugin.json` — `frontend_entrypoint` actualizado a `.tsx`.

### Endpoints finales

| Method | Path | Acción |
|--------|------|--------|
| GET | `/server/status` | estado mail server |
| POST | `/server/ensure` | provisionar mail server |
| GET | `/domains` | listar dominios mail |
| POST | `/domains` | agregar dominio mail |
| DELETE | `/domains/{id}` | eliminar dominio mail |
| GET | `/mailboxes` | listar buzones |
| POST | `/mailboxes` | crear buzón (retorna password) |
| DELETE | `/mailboxes/{id}` | eliminar buzón |

## OUT OF SCOPE

- Webmail (roundcube, snappymail).
- Aliases, forwards, filters.
- DKIM/SPF/DMARC configuration.
- SSL para mail (STARTTLS via docker-mailserver).

## CONTRACT

- PRE: mail plugin instalado y habilitado; docker remoto accesible.
- POST: CRUD completo de dominios y buzones; mail server se provisiona
  bajo demanda; contraseña se muestra una sola vez; eliminación limpia
  en DB y container.
- DELETE domain: si tiene buzones asociados → 409 (no se puede eliminar
  dominio con buzones).
- DELETE mailbox: ejecuta `setup email del` en container antes de
  eliminar de DB.

## INVARIANTS

```yaml
invariants:
  - Tenant isolation: solo dominios/buzones del tenant actual.
  - Dominio mail duplicado → 409.
  - Dominio con buzones → 409 al eliminar.
  - Contraseña de buzón se entrega una sola vez.
  - Kernel MUST NOT ser modificado.
```

## VERIFICATION

```bash
# provisionar mail server
curl -X POST http://localhost:8001/api/v1/plugins/mail/server/ensure \
  -H "Authorization: Bearer $TOKEN"

# agregar dominio
curl -X POST http://localhost:8001/api/v1/plugins/mail/domains \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"domain":"example.com"}'

# crear buzón
curl -X POST http://localhost:8001/api/v1/plugins/mail/mailboxes \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"domain":"example.com","user":"admin"}'

# listar
curl http://localhost:8001/api/v1/plugins/mail/domains \
  -H "Authorization: Bearer $TOKEN"
curl http://localhost:8001/api/v1/plugins/mail/mailboxes \
  -H "Authorization: Bearer $TOKEN"

# eliminar
curl -X DELETE http://localhost:8001/api/v1/plugins/mail/mailboxes/<id> \
  -H "Authorization: Bearer $TOKEN"
curl -X DELETE http://localhost:8001/api/v1/plugins/mail/domains/<id> \
  -H "Authorization: Bearer $TOKEN"
```

## ROLLBACK

Eliminar endpoints GET/DELETE; frontend MailView y register revertido
a vacío. Tablas `mail_domain` y `mailbox` intactas.

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/mail/backend/plugin.py
    - plugins/mail/frontend/register.tsx
    - plugins/mail/frontend/MailView.tsx
    - plugins/mail/plugin.json
  prohibited:
    - vendor/**
    - apps/web/**
    - plugins/docker-infra/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - mail.domains
    - mail.mailboxes
    - mail.server
  indirect:
    - docker-mailserver container (exec del)
  must_not_affect:
    - kernel.auth
    - docker_infra
    - hosting
    - proxy
```

## Traceability

- Requirement: extensión de plugin mail existente.
- Commit: (pending)

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
