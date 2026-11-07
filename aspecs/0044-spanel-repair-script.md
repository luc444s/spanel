# A.SPEC 0044 — spanel-repair: script autónomo de auto-reparación, agnóstico al server

> `risk: normal` — solo agrega `scripts/spanel-repair.py`, unit files systemd y
> tests. No toca backend del panel, publisher ni traefik; las acciones de
> "fix" son opt-in (`--fix`) y con dry-run por defecto.

## WHY

El caso real de hoy (core.systutor.com sin cert): la cadena fue
**Cloudflare proxy → NXDOMAIN → 404 del challenge ACME por prioridad del
router**, y el operador humano solo se enteró porque el sitio se quedó sin
TLS. La solución definitiva no debía depender de la intervención manual, ni
del proveedor DNS, ni de detalles de UN server (paths, nombres de container,
resolver, dominios fijos, IP técniva).

Cuya premisa: los síntomas conocidos son marcas deterministas (issuer default,
rules sin exclusión de challenge, routers sin resolver, yml huérfanos) y son
auto-detectables **localmente** con docker CLI, openssl y YAML — sin tocar
nubes ni DNS providers.

## WHAT

1. **`scripts/spanel-repair.py`** — un archivo, stdlib-only (sin pip), dos modos:
   - `check` (default): solo reporta. Imprime tabla de hallazgos + acción
     propuesta; exit 0 si todo ok, exit 1 si hay pendientes.
   - `--fix`: ejecuta las acciones de reparación seguras, con cooldown.
   - `--json`: salida máquina-parsable (para alertas futuras).
2. **Agnóstico — autodetección con override (`flag > env > detect > error`)**:
   - Contenedor traefik: buscar contenedor con imagen `traefik` o command
     `traefik*`. Override `--traefik-container` / `SPANEL_TRAEFIK_CONTAINER`.
   - Dynamic config dir: del `--providers.file.directory` en Args/traefik.yml
     del contenedor (probar ambos en-mount); fallback env
     `SPANEL_DYNAMIC_DIR`. Override `--config-dir`.
   - **Resolvers**: leer todos los `certificatesResolvers` del traefik.yml del
     contenedor; no hay assumption del nombre. Si hay varios, se respetan los
     que cada router ya refiere. Override `--resolver`.
   - Dominios: TODOS los routers spanel publicados (`spanel-*.yml`), no lista
     fija. `--only-host` para enfocar una falla específica.
   - IP pública del server: sin curl ni APIs externas — truco de `socket`
     saliente para determinar la IP local de salida, fallback
     `SPANEL_PUBLIC_IP` / `--public-ip`. Funciona tras NAT.
   - Docker: respeta `DOCKER_HOST` naturalmente porque se usa el CLI `docker`
     (host, mac, ThinkPad, server — cualquier host).
3. **Reglas de detección/acción** (cada una con checks deterministas):
   - **R1 — challenge interceptado**: para cada archivo `spanel-*.yml`
     publicado: en el router `entryPoints: [web]`, la `rule` debe excluir
     `!(PathPrefix(/.well-known/acme-challenge))`. Si no, patch atómico con
     `.bak.<ts>` al lado. Restart de traefik solo si hubo patch, con cooldown
     de `--cooldown 3600`.
   - **R2 — cert default sirviendo**: por cada router websecure con
     `tls.certResolver`, `openssl s_client -connect host:443 -servername host`.
     Si el issuer contiene "TRAEFIK DEFAULT CERT":
     1) resolver público: si el dominio NO apunta a la IP pública local →
        PENDIENTE (instrucción en log; arreglar el A record es humano).
     2) Si DNS sí apunta: registrarlo en un state file y (con cooldown)
        `docker restart <traefik>` para re-disparar el challenge.
   - **R3 — router websecure sin `tls.certResolver`**: patch (agregar el
     resolver referido por otros routers spanel, o el único existente)
     + restart con cooldown.
   - **R4 — ruta huérfana**: cross-check `docker ps --format` contra archivos
     publicados; si el contenedor ya no existe: mover el yml a
     `<dir>/.orphans/` (no borrar) con --fix.
   - Cualquier caso no automatizable queda en el log como
     `PENDIENTE: <instrucción concreta>` (CF naranja activo, rate limit LE,
     A record mal, etc.).
4. **Log único**: `/var/log/spanel-repair.log` (o `--log-file`); una línea por
   hallazgo con timestamp + acción + outcome, JSON con `--json`.
5. **`scripts/systemd/spanel-repair.service` / `.timer`** — el mismo patrón de
   `mail-cert.service/.timer` (timer cada 15 min). Opcional: si systemd no
   existe (o no es root), el script corre manual y sale.
6. **Tests**: `scripts/tests/test_spanel_repair.py` (pytest-able y standalone
   runnable como `test_mail_cert_live.py`): detección de R1 (regla sin
   exclusión → patch correcto), R2 fake (issuer default → decide DNS/restart
   correctamente con public-ip match/mismatch), R3, R4, autodetección con
   docker inspect fake (args+mounts → config dir y resolver en dos servers
   "distintos"), dry-run default (ninguna escritura sin --fix), cooldown (no
   segundo restart), y forma del output JSON. Sin red real: todo fake.

## SCOPE

Entra:
- `scripts/spanel-repair.py` (nuevo)
- `scripts/systemd/spanel-repair.service`, `scripts/systemd/spanel-repair.timer` (nuevos)
- `scripts/tests/test_spanel_repair.py` (nuevo)
- `aspecs/0044-spanel-repair-script.md`

No entra:
- Cambios en backend del panel, publisher de spanel, provisioner, kernel,
  plugins vendor (nada), docker-compose.yml.
- Integraciones de DNS providers (CF token, etc.), Cloudflare origin certs.
- UI del panel (estado de cert en la UI queda para una spec propia si se pide).
- Notificaciones (email/telegram/etc.) — solo log.

## CONTRACT

Precondiciones:
- Host con acceso docker (o docker CLI agresivo roles) con un contenedor
  traefik corriendo; unit files systemd opcional (funciona manual).

Postcondiciones:
1. `python3 spanel-repair.py` en un server sano: exit 0 sin tocar nada.
2. Router de site HTTP sin exclusion de challenge + `--fix` → yml parcheado
   (con .bak) y restart (freetranss cooldown) y el challenge ACME pasa.
3. Router sin resolver + `--fix` → tls.certResolver agregado y restart.
4. Dominio sin cert y DNS público apuntando mal → nunca reinicia; reporta `PENDIENTE`.
5. Ruta con contenedor muerto + `--fix` → archivo movido a `.orphans/`, no borrado.
6. Sin `--fix` el script NO escribe ni reinicia nada (dry-run por defecto).
7. Montado en cualquier server nuevo (paths/nombres/duelo distinctos) funciona
   con cero config, o con un solo flag cuando sea intrínseco el caso.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: Nada hardcodeado de un server concreto (paths, nombres, resolver, IP, dominios).
    proof: tests con fixtures de dos servers "distintos" (mock docker inspect YAML distintos) que resuelven correcto con cero flags.
  - id: I2
    statement: Dry-run por defecto; ninguna escritura/reinio sin --fix.
    proof: test_check does_not_write, no restart invocado.
  - id: I3
    statement: El restart de traefik nunca ocurre más de una vez por cooldown.
    proof: cooldown state file persistido; test_double_cooldown.
  - id: I4
    statement: Los patch de YAML son atomicos y reversibles (.bak con timestamp).
    proof: test_patch_creates_bak; patch via tmp+os.replace.
  - id: I5
    statement: Un dominio con DNS mal punto NUNCA dispara restart; solo PENDIENTE.
    proof: test_r2_dns_mismatch_no_restart.
  - id: I6
    statement: stdlib only; sin pip.
    proof: import check en CI; grep de third-party imports.
  - id: I7
    statement: La salida es legible por máquina con --json (exit codes estables: 0 ok, 1 pendientes, 2 error).
    proof: test_json_output_shape.
```

## VERIFICATION

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin && PYTHONPATH=. .venv/bin/python -m pytest scripts/tests/test_spanel_repair.py -q
cd /data/data/com.termux/files/home/Proyectos/mailadmin && .venv/bin/python -m ruff check scripts/spanel-repair.py scripts/tests/test_spanel_repair.py
```

Smoke local sin Docker (no debe explotar): el script debe manejar host sin
traefik con PENDIENTE informativo y exit 0/1, nunca crash.

Instalación (documentada en el docstring del script; la ejecuta el operador):

```bash
scp scripts/spanel-repair.py scripts/systemd/spanel-repair.service \
    scripts/systemd/spanel-repair.timer root@server:/opt/spanel/
ssh root@server 'install -m 755 /opt/spanel/spanel-repair.py /usr/local/sbin/ ;
  cp /opt/spanel/spanel-repair.service /opt/spanel/spanel-repair.timer /etc/systemd/system/ ;
  systemctl daemon-reload && systemctl enable --now spanel-repair.timer'
```

## ROLLBACK

`systemctl disable --now spanel-repair.timer && rm /usr/local/sbin/spanel-repair.py` Revert del repo. Los patch de YAML que el script haya hecho quedan con su `.bak` y son reversibles manualmenta.

## Change Surface

```yaml
change_surface:
  allowed:
    - scripts/spanel-repair.py
    - scripts/systemd/spanel-repair.service
    - scripts/systemd/spanel-repair.timer
    - scripts/tests/test_spanel_repair.py
    - aspecs/0044-spanel-repair-script.md
  prohibited:
    - vendor/**
    - apps/**
    - docker-compose.yml
    - .env*
```

## Traceability

- Requirement: scripts autónomos que detecten y reparen los fallos conocidos de TLS/rutas de spanel en cualquier server (incl. el caso real core.systutor.com), sin depender de Cloudflare/u otros DNS providers, sin hardcode de un server, con dry-run y cooldown.
- owner: luc444s
- Deployment: manual en server (scp + systemd enable), timer 15 min.

## Definition of Done

- [ ] `python3 spanel-repair.py` autodetecta traefik/config-dir/resolvers/domains/IP en fixtures de dos servers distintos con cero flags.
- [ ] R1: patch de exclusión de challenge + restart con cooldown.
- [ ] R2: default cert → decide DNS-apuntado-bien vs mal; restart solo si corresponde, PENDIENTE si no.
- [ ] R3 y R4 implementados y teste.
- [ ] Dry-run por defecto; exit codes 0/1/2; --json.
- [ ] Tests verdes y ruff limpio; stdlib-only.
