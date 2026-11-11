# SPANEL-SEC-2026-001 — Containment and verification

English | [Español](spanel-sec-2026-001.es.md)

Status: **Phase 1 containment applied in the repository**. It still needs to be deployed on the
VPS, and Phase 2 (network segmentation) completed, which is the phase that eliminates the confirmed
lateral access.

Scope of the change in `docker-compose.yml`:

- `ports: ["3000:3000"]` is removed from the `app` service. The panel no longer
  listens on the host, either on IPv4 or IPv6.
- Exposure through Traefik is preserved (`proxy` network, `web`/`websecure` labels).
- HTTP → HTTPS redirection is added on the `spanel-http` router, so that
  login never travels in plain text.

> Important: removing `ports:` does **not** eliminate lateral access. `app` remains
> joined to the `proxy` network, and customer sites connect to that same network
> (`docker network connect <proxy> <site>`). As long as they share a network,
> `legalattorney-clean` can still reach `app:3000`. Phase 2 separates
> those networks.

---

## Phase 1 — Unpublish the port (run on the VPS)

From the stack's actual directory (where the `docker-compose.yml` lives):

```bash
# 1. View the current state: 0.0.0.0:3000->3000/tcp should appear
docker compose ps
docker port "$(docker compose ps -q app)" 3000

# 2. Recreate only the panel with the corrected Compose file
docker compose up -d --no-deps app

# 3. Confirm that there is no publication anymore
docker port "$(docker compose ps -q app)" 3000 || echo "OK: no published port"
docker ps --filter "name=$(docker compose ps --format '{{.Name}}' app)" \
  --format '{{.Names}}  {{.Ports}}'
```

Expected result: `3000/tcp` without `0.0.0.0:3000->` or `[::]:3000->`.

### Verification on the host

```bash
ss -lntp  | grep -w 3000 || echo "OK: 3000 is not listening on IPv4"
ss -lntp6 | grep -w 3000 || echo "OK: 3000 is not listening on IPv6"
```

### Verification from outside

From another machine (not from the VPS):

```bash
curl -sS -m 5 http://<PUBLIC_IP>:3000/ ; echo "exit=$?"
# Expected: timeout / connection refused (exit != 0)

curl -sS -o /dev/null -w '%{http_code}\n' https://spanel.systutor.com/
# Expected: 200 or 30x

curl -sSI http://spanel.systutor.com/ | grep -i '^location:'
# Expected: https://spanel.systutor.com/...
```

### Lateral access verification (still open until Phase 2)

```bash
APP_IP=$(docker inspect -f \
  '{{range $n,$c := .NetworkSettings.Networks}}{{println $n $c.IPAddress}}{{end}}' \
  "$(docker compose ps -q app)")
echo "$APP_IP"

docker exec legalattorney-clean sh -c \
  'getent hosts app; curl -sS -m 5 -o /dev/null -w "HTTP %{http_code}\n" http://app:3000/api/v1/system/health'
```

If it responds, the lateral finding is confirmed and is fixed in Phase 2.

---

## Phase 2 — Isolate Spanel from customer containers

Goal: Spanel shares a network **only with Traefik**, never with hosted sites
or apps deployed by customers.

1. Create a dedicated network and connect Traefik:

```bash
docker network create spanel_ingress
docker network connect spanel_ingress traefik
```

2. Persist that connection in Traefik's Compose file (so it survives a
   `docker compose up -d` of the proxy):

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

3. In Spanel's Compose file, replace the `proxy` network of the `app` service:

```yaml
  app:
    networks:
      - default
      - spanel_ingress
    labels:
      - "traefik.docker.network=spanel_ingress"
      # ...rest of the labels unchanged...

networks:
  proxy:
    external: true          # still used by customer sites
  spanel_ingress:
    external: true          # created in step 1
```

4. Recreate the panel and verify:

```bash
docker compose up -d --no-deps app

# From legalattorney-clean, resolution/connection must fail:
docker exec legalattorney-clean sh -c \
  'getent hosts app || echo "OK: app does not resolve"; curl -sS -m 5 http://app:3000/ || echo "OK: no access"'

# The ingress network must still reach the panel:
docker run --rm --network spanel_ingress curlimages/curl \
  -sS -m 5 -o /dev/null -w 'HTTP %{http_code}\n' http://app:3000/api/v1/system/health

# And the panel must still respond through Traefik from outside:
curl -sS -o /dev/null -w '%{http_code}\n' https://spanel.systutor.com/
```

Rollback: add `- proxy` back to the `app` service's `networks` and
`traefik.docker.network=proxy`, then `docker compose up -d --no-deps app`.

---

## Phase 3 — Reduce impact if the API is compromised

Pending, requires a change window:

- The container runs as **root** and mounts `/var/run/docker.sock` with
  write access. Any RCE in the API is equivalent to root on the host. The only real
  mitigation is that the panel is not reachable from untrusted workloads
  (Phase 2) and that the socket is not exposed to other paths.
- Evaluate a `docker-socket-proxy` with an endpoint allowlist. It is partial: the
  panel needs to create containers and connect networks, so it retains power
  equivalent to root. It does not replace network isolation.
- Run as a non-root user with the socket group (reduces impact
  inside the container, not that of the socket).
- There is no rate limiting or lockout for failed attempts on `/auth/login`.
  Adding it reduces the brute-force window against the published panel.
- Verify that the Traefik dashboard and other services are not published
  outside `127.0.0.1`.

---

## Closure criteria

- [ ] `3000/tcp` does not appear published in `docker ps`.
- [ ] `ss -lntp` / `ss -lntp6` do not show 3000 on the host.
- [ ] `curl http://<IP>:3000` from outside fails.
- [ ] `https://spanel.systutor.com` responds and `http://` redirects.
- [ ] `legalattorney-clean` does not resolve or connect to `app:3000` (Phase 2).
- [ ] Traefik still reaches the panel after Phase 2.
