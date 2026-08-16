# A.SPEC SP-0023 — HTTP puro + Tailscale (modo dual)

## WHY

Spanel debe funcionar en dos modos:
- **HTTP puro (producción VPS)**: dominios públicos → Traefik → containers.
  Acceso desde internet sin Tailscale.
- **Tailscale (desarrollo)**: hostnames `.ts.net` → Traefik → containers.
  Acceso solo dentro de la tailnet.

Problema actual: WordPress no conoce su URL pública hasta que se configura
un dominio. El provisioning crea containers sin siteurl/home, y el plugin
proxy no actualiza WordPress cuando se agrega un dominio.

## WHAT

Transición observable: WordPress siempre conoce su URL pública. Al
provisionar con dominio, siteurl/home se configuran automáticamente.
Al agregar dominio via proxy plugin, WordPress se actualiza. Sin
hardcoded hostnames — todo basado en el dominio configurado.

## SCOPE

### 1. Hosting: domain en provisioning

`ProvisionWordpressRequest` agrega campo opcional `domain: str | None`.

Si se provee `domain`:
- Después de crear el container WP, ejecutar:
  ```sql
  UPDATE wp_options SET option_value='http://{domain}' WHERE option_name IN ('siteurl','home');
  ```
  via `docker exec mariadb ...` sobre el container DB del site.
- Agregar el dominio a `domains_json` del site.
- Registrar el dominio en `hosting_domain` (tabla del proxy plugin).
- Crear la ruta en Traefik (usando proxy plugin).

Si no se provee `domain`: comportamiento actual (sin siteurl configurado).

### 2. Proxy: sync WordPress al agregar dominio

En `POST /domains` del proxy plugin, después de crear el dominio:
- Si el site es WordPress (stack = 'wordpress'), actualizar siteurl/home
  en la DB de WordPress al nuevo FQDN.
- Usar `docker exec mariadb` sobre `db_container_name` del site.

En `DELETE /domains` del proxy plugin:
- Si el dominio eliminado era el siteurl de WordPress, no hacer nada
  (WordPress queda con la URL vieja — admin puede corregir manual).

### 3. Sin hardcoded hostnames

- Eliminar cualquier referencia a `lucas-thinkpad-e570.tail8a6288.ts.net`
  o `100.100.26.58` en configs de Traefik.
- Traefik usa solo los dominios configurados en `hosting_domain`.
- forwardAuth address usa `host.docker.internal` o la IP del container
  Spanel (no una IP Tailscale hardcoded).

### 4. Entrypoints Traefik

Traefik ya escucha en `:80` (web) y `:443` (websecure). En producción
VPS, los puertos 80/443 están expuestos a internet. En desarrollo
Tailscale, solo accesibles dentro de la tailnet. No hay cambios en
Traefik — solo en los dominios que se configuran.

## CONTRACT

- PRE: Docker remoto accesible; Traefik corriendo; plugin hosting y
  proxy habilitados.
- POST: WordPress siempre con siteurl/home correcto; funcionamiento
  transparente en HTTP puro y Tailscale; sin IPs/hostnames hardcoded.
- domain en provisioning → WP configurado + Traefik route creado.
- domain sin provisioning → WP configurado al agregar dominio.

## INVARIANTS

```yaml
invariants:
  - WordPress siteurl SIEMPRE refleja el dominio configurado.
  - Sin hardcoded hostnames en Traefik configs.
  - forwardAuth usa dirección accesible desde el container Traefik.
  - Modo HTTP puro y Tailscale usan la misma lógica.
  - Kernel MUST NOT ser modificado.
```

## VERIFICATION

```bash
# 1. Provisionar WP con dominio
curl -X POST http://localhost:8001/api/v1/plugins/hosting/sites/provision/wordpress \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"test","admin_email":"admin@test.com","domain":"test.example.com"}'

# 2. Verificar siteurl en WP
docker exec spanel-test-db mariadb -u wp -p<pass> wordpress \
  -e "SELECT option_name,option_value FROM wp_options WHERE option_name IN ('siteurl','home')"

# 3. Verificar Traefik route
docker exec spanel-traefik cat /etc/traefik/dynamic/test.yml

# 4. Agregar dominio a site existente
curl -X POST http://localhost:8001/api/v1/plugins/proxy/domains \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"fqdn":"nuevo.example.com","site_id":"<id>"}'

# 5. Verificar que WP se actualizó
docker exec spanel-test-db mariadb -u wp -p<pass> wordpress \
  -e "SELECT option_name,option_value FROM wp_options WHERE option_name IN ('siteurl','home')"

# 6. Acceder desde internet (VPS)
curl -sI http://test.example.com | head -3

# 7. Acceder desde Tailscale (dev)
curl -sI http://lucas-thinkpad-e570.tail8a6288.ts.net | head -3
```

## ROLLBACK

Revertir `ProvisionWordpressRequest` sin campo domain; revertir sync
WP en proxy plugin POST/DELETE domains. Tablas intactas.

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/hosting/backend/plugin.py
    - plugins/proxy/backend/plugin.py
  prohibited:
    - vendor/**
    - apps/web/**
    - plugins/docker-infra/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - hosting.provision
    - proxy.domains
  indirect:
    - WordPress siteurl/home (DB remota)
    - Traefik dynamic config
  must_not_affect:
    - kernel.auth
    - docker_infra
    - mail plugin
```

## Traceability

- Requirement: compatibilidad HTTP puro + Tailscale.
- Commit: (pending)

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
