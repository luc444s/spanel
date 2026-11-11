# SPANEL-SEC-2026-001 — Contención y verificación

[English](spanel-sec-2026-001.md) | Español

Estado: **contención Fase 1 aplicada en el repositorio**. Falta desplegarla en el
VPS y completar la Fase 2 (segmentación de red), que es la que elimina el acceso
lateral confirmado.

Alcance del cambio en `docker-compose.yml`:

- Se elimina `ports: ["3000:3000"]` del servicio `app`. El panel deja de
  escuchar en el host, tanto en IPv4 como en IPv6.
- Se conserva la exposición por Traefik (`proxy`, labels `web`/`websecure`).
- Se agrega redirección HTTP → HTTPS en el router `spanel-http`, para que el
  login nunca viaje en texto plano.

> Importante: quitar `ports:` **no** elimina el acceso lateral. `app` sigue
> unida a la red `proxy`, y los sitios de clientes se conectan a esa misma red
> (`docker network connect <proxy> <sitio>`). Mientras compartan red,
> `legalattorney-clean` puede seguir llegando a `app:3000`. La Fase 2 separa
> esas redes.

---

## Fase 1 — Despublicar el puerto (rodar en el VPS)

Desde el directorio real del stack (donde vive el `docker-compose.yml`):

```bash
# 1. Ver el estado actual: debe aparecer 0.0.0.0:3000->3000/tcp
docker compose ps
docker port "$(docker compose ps -q app)" 3000

# 2. Recrear solo el panel con el Compose corregido
docker compose up -d --no-deps app

# 3. Confirmar que ya no hay publicación
docker port "$(docker compose ps -q app)" 3000 || echo "OK: sin puerto publicado"
docker ps --filter "name=$(docker compose ps --format '{{.Name}}' app)" \
  --format '{{.Names}}  {{.Ports}}'
```

Resultado esperado: `3000/tcp` sin `0.0.0.0:3000->` ni `[::]:3000->`.

### Verificación en el host

```bash
ss -lntp  | grep -w 3000 || echo "OK: 3000 no escucha en IPv4"
ss -lntp6 | grep -w 3000 || echo "OK: 3000 no escucha en IPv6"
```

### Verificación desde fuera

Desde otra máquina (no desde el VPS):

```bash
curl -sS -m 5 http://<IP_PUBLICA>:3000/ ; echo "exit=$?"
# Esperado: timeout / connection refused (exit != 0)

curl -sS -o /dev/null -w '%{http_code}\n' https://spanel.systutor.com/
# Esperado: 200 o 30x

curl -sSI http://spanel.systutor.com/ | grep -i '^location:'
# Esperado: https://spanel.systutor.com/...
```

### Verificación del acceso lateral (sigue abierto hasta Fase 2)

```bash
APP_IP=$(docker inspect -f \
  '{{range $n,$c := .NetworkSettings.Networks}}{{println $n $c.IPAddress}}{{end}}' \
  "$(docker compose ps -q app)")
echo "$APP_IP"

docker exec legalattorney-clean sh -c \
  'getent hosts app; curl -sS -m 5 -o /dev/null -w "HTTP %{http_code}\n" http://app:3000/api/v1/system/health'
```

Si responde, el hallazgo lateral está confirmado y se corrige en la Fase 2.

---

## Fase 2 — Aislar Spanel de los contenedores de clientes

Objetivo: que Spanel comparta red **solo con Traefik**, nunca con los sitios
alojados ni con apps desplegadas por clientes.

1. Crear una red dedicada y conectar Traefik:

```bash
docker network create spanel_ingress
docker network connect spanel_ingress traefik
```

2. Persistir esa conexión en el Compose de Traefik (para que sobreviva a un
   `docker compose up -d` del proxy):

```yaml
services:
  traefik:
    networks:
      - proxy
      - spanel_ingress

networks:
  spanel_ingress:
    external: true
```

3. En el Compose de Spanel, reemplazar la red `proxy` del servicio `app`:

```yaml
  app:
    networks:
      - default
      - spanel_ingress
    labels:
      - "traefik.docker.network=spanel_ingress"
      # ...resto de labels iguales...

networks:
  proxy:
    external: true          # lo siguen usando los sitios de clientes
  spanel_ingress:
    external: true          # creada en el paso 1
```

4. Recrear el panel y verificar:

```bash
docker compose up -d --no-deps app

# Desde legalattorney-clean debe fallar la resolución/conexión:
docker exec legalattorney-clean sh -c \
  'getent hosts app || echo "OK: app no resuelve"; curl -sS -m 5 http://app:3000/ || echo "OK: sin acceso"'

# La red de ingreso sí debe alcanzar al panel:
docker run --rm --network spanel_ingress curlimages/curl \
  -sS -m 5 -o /dev/null -w 'HTTP %{http_code}\n' http://app:3000/api/v1/system/health

# Y el panel debe seguir respondiendo por Traefik desde fuera:
curl -sS -o /dev/null -w '%{http_code}\n' https://spanel.systutor.com/
```

Rollback: volver a agregar `- proxy` a `networks` del servicio `app` y
`traefik.docker.network=proxy`, luego `docker compose up -d --no-deps app`.

---

## Fase 3 — Reducir impacto si la API se compromete

Pendiente, requiere ventana de cambio:

- El contenedor corre como **root** y monta `/var/run/docker.sock` con
  escritura. Cualquier RCE en la API equivale a root en el host. La única
  mitigación real es que el panel no sea alcanzable desde cargas no confiables
  (Fase 2) y que el socket no se exponga a otras rutas.
- Evaluar un `docker-socket-proxy` con allowlist de endpoints. Es parcial: el
  panel necesita crear contenedores y conectar redes, así que conserva poder
  equivalente a root. No sustituye el aislamiento de red.
- Ejecutar como usuario no-root con el grupo del socket (reduce el impacto
  dentro del contenedor, no el del socket).
- No hay rate limiting ni bloqueo por intentos fallidos en `/auth/login`.
  Agregarlo reduce la ventana de fuerza bruta sobre el panel publicado.
- Revisar que el dashboard de Traefik y otros servicios no estén publicados
  fuera de `127.0.0.1`.

---

## Criterio de cierre

- [ ] `3000/tcp` no aparece publicado en `docker ps`.
- [ ] `ss -lntp` / `ss -lntp6` no muestran 3000 en el host.
- [ ] `curl http://<IP>:3000` desde fuera falla.
- [ ] `https://spanel.systutor.com` responde y `http://` redirige.
- [ ] `legalattorney-clean` no resuelve ni conecta a `app:3000` (Fase 2).
- [ ] Traefik sigue llegando al panel tras la Fase 2.
