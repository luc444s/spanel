# A.SPEC SP-0011 — Proxy, domains and SSL (traefik)

## WHY

Un sitio web sin dominio público y TLS no es un sitio. Spanel debe rutear
`fqdn → container:puerto` con SSL automático. Traefik ya elegido
(arquitectura-base.md §7, decisión 1).

## WHAT

Una transición observable: admin asocia un dominio a un Site; Spanel
configura traefik (labels docker o provider file) para rutear el dominio
al backend del container, emite certificado letsencrypt y reporta estado
SSL del dominio.

## SCOPE

- Plugin `proxy`: adoptar/provisionar traefik en el docker remoto (si no
  existe), red compartida `spanel-proxy`.
- Tabla `domain` en hosting (fqdn, site_id, ssl_status, expires).
- `POST /api/v1/plugins/hosting/domains` {site_id, fqdn} → aplica labels
  al container del site (o file provider + reload traefik).
- `GET /api/v1/plugins/hosting/domains/{id}` con estado SSL vivo
  (certificado desde traefik API).
- Certbot/letsencrypt via traefik resolver (http-01, wildcard fuera).

## OUT OF SCOPE

- Gestión DNS externa (SP futura; DNS se apunta manual o por proveedor).
- Wildcard certs, múltiples backends por dominio.
- forwardAuth de sidecars (SP-0019).

## CONTRACT

- PRE: site adoptado con puerto interno conocido; traefik operable en el
  remoto.
- POST: dominio responde en 80/443 apuntando al backend correcto; SSL
  emitido y renovable; estado visible por dominio; dominio duplicado →
  409; fallo de traefik → error tipado sin romper la API.

## INVARIANTS

```yaml
invariants:
  - Dominios de containers NO adoptados MUST NOT tocarse.
  - Rutas proxy ajenas (de containers externos) MUST NOT alterarse.
  - Tenant isolation MUST permanecer intacta.
  - Kernel MUST NOT ser modificado.
```

## VERIFICATION

```bash
# tras configurar dominio test.<ts>.ts.net:
curl -sI https://test.<dominio> | head -3   # 200 + cert válido
curl -s http://127.0.0.1:8001/api/v1/plugins/hosting/domains -H "Authorization: Bearer $TOKEN" | jq
```

## ROLLBACK

Quitar labels/entries del provider + `traefik` reload; fila domain
eliminada.

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/proxy/**
    - plugins/hosting/**
  prohibited:
    - vendor/**
    - apps/web/**
    - plugins/docker-infra/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - proxy.routing
  indirect:
    - traefik.config (remoto)
  must_not_affect:
    - kernel.auth
    - routing externo existente
    - containers no adoptados
```

## Traceability

- Requirement: arquitectura-base.md §7.
- Commit: root 5fb74f5
- Deployment: `npm run services:no-reload`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
