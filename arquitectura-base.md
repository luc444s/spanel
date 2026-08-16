# Spanel — Arquitectura base

Documento orquestador. Define qué es Spanel y cómo se construye. Los
cambios se materializan en A.SPECs (`spec/SP-XXXX-*.aspec.md`), que
referencian este documento como requisito.

## 1. Qué es Spanel

Administrador de sitios web sobre Docker remoto. Enfoque primario:
WordPress y docker mail. Soportará también stacks genéricos (PHP, Astro,
estáticos, proxy-only).

Capacidades núcleo:

- Descubrir containers Docker ya creados e integrarlos (adopt) sin tocarlos
- Administrar sitios: lifecycle, dominios, SSL, archivos, backups
- Entrar a wp-admin con credenciales desde el dashboard (SSO)
- Administrar mail: dominios, buzones, aliases
- Todo auditado, multi-tenant (kernel SYSTUTOR)

## 2. Filosofía

- Spanel es **host app** del ecosistema SYSTUTOR. El kernel (auth, RBAC,
  tenants, audit, eventos, plugin runtime) es estable y no se toca.
- **Todo dominio vive en plugins.** Spanel solo compone menú, branding y
  gate de auth.
- Docker es infraestructura de transporte, no negocio → adapter
  compartido, no acoplado a hosting.
- **Adoptar primero, provisionar después.** Nada se toca sin adopción
  explícita.
- Read-only por defecto: discovery jamás modifica containers.

## 3. Arquitectura por capas

```text
Spanel (host app)
├── consola admin kernel            ← estable (SP-0001..0006)
└── plugins/
    ├── docker-infra/               ← adapter docker remoto + discovery + adopt
    │   ├── adapter: ssh → docker CLI (host x86 Tailscale)
    │   ├── service: containers, volumes, networks, exec, compose
    │   └── eventos: container.discovered, container.state_changed
    ├── hosting/                    ← dominio sitios web
    │   ├── site: adopt/provision/start/stop/logs
    │   ├── stack: wordpress | php | static | proxy-only
    │   ├── domain: fqdn + DNS + SSL
    │   ├── files: filebrowser sidecar por sitio
    │   ├── backup: wp-content + db dump
    │   └── wp-cli: exec en container (updates, plugins, users)
    ├── proxy/                      ← traefik: rutas, TLS letsencrypt,
    │                                forwardAuth (JWT Spanel) para sidecars
    └── mail/                       ← docker-mailserver
        ├── dominios, buzones, aliases, DKIM/SPF
        └── evento mail.smtp_provisioned (wp lo consume para SMTP)
```

## 4. Modelo de recursos

| Recurso | Descripción |
|---------|-------------|
| Server | host docker remoto (hoy 1 en Tailscale, mañana N) |
| Site | stack, containers, volúmenes, dominios, db (si wp), estado |
| Domain | fqdn → site, SSL status |
| MailDomain / Mailbox / Alias | mail del tenant |

## 5. Discovery (buscar dockers creados)

Docker remoto = fuente de verdad. Spanel = espejo registrado.

```text
docker ps -a + inspect      → snapshot (en vivo, read-only)
  ↓
clasificar por señales      → inferir stack y relaciones
  ↓
diff contra lo registrado   → reconciliación
  ↓
candidatos sin dueño        → "descubiertos"
  ↓
adopt (SP-0009)             → Site registrado + tenant/branch
```

Señales de clasificación:

| Señal | Infiere |
|-------|---------|
| image `wordpress*` / `php*` | stack wordpress/php |
| image `mariadb/mysql/postgres` | db candidata |
| red docker compartida con db | "este wp usa esa db" |
| labels traefik/caddy | dominios ya expuestos + ruta proxy |
| `com.docker.compose.project` | agrupar por proyecto compose |
| puertos publicados | servicios expuestos (mail, db...) |

Reconciliación: container nuevo → `container.discovered`; registrado pero
ausente → `site.orphaned`; up↔stopped → `container.state_changed`.
El estado vivo nunca se cachea: consulta directa.

Estados de un container:

```text
discovered → adopted → managed
               ↓
            orphaned (murió por fuera)
```

## 6. WordPress

- **DB**: 1 container por sitio (aislamiento, backup trivial)
- **Provision**: imagen oficial wordpress + mariadb, traefik label,
  wp-cli para hardening e instalación del plugin SSO
- **SSO wp-admin**: magic link firmado por Spanel (JWT, expiración corta)
  → plugin `spanel-sso` en el WP valida → `wp_set_auth_cookie` →
  redirect. Spanel guarda email admin, NUNCA password del wp
- **Files**: filebrowser sidecar sobre wp-content, auth via traefik
  forwardAuth (JWT Spanel), auditado por kernel
- **Backups**: wp-content (tar) + db (dump) → volumen/almacenamiento

## 7. Proxy y dominios

- Traefik como router: labels docker para rutas, TLS letsencrypt,
  forwardAuth para servicios internos (filebrowser, dashboards)
- Todo dominio declarado en Site; SSL estado visible
- Access logs traefik → Spanel (analítica de accesos, SP-0018)

## 8. Mail

- docker-mailserver (1 container, liviano) — adopt o provision
- Dominios, buzones, aliases, DKIM/SPF
- Evento `mail.smtp_provisioned`: los wp consumen SMTP sin configuración
  manual

## 9. Frontend

- Vista de plugins montada por `frontend_entrypoint` del manifest —
  fuera del menú principal del host
- Host aporta: login, layout, menú de módulos registrados por plugins
- Consola admin kernel ya existe (SP-0001..0006)

## 10. Roadmap de specs

Las A.SPECs se crean bajo `spec/` conforme se implementan. Este roadmap
es el orden previsto, no specs pre-aprobadas.

| Spec | Objetivo |
|------|----------|
| SP-0008 | docker-infra plugin (adapter + service) |
| SP-0009 | adopt container → site |
| SP-0010 | site lifecycle (start/stop/restart/logs) |
| SP-0011 | proxy + dominios + SSL (traefik) |
| SP-0012 | provision wordpress (wp + db + wp-cli) |
| SP-0013 | mail: adopt/provision + dominios/buzones |
| SP-0014 | backups wp (archivos + db) |
| SP-0015 | plugin frontend runtime (UI dinámica) |
| SP-0016 | detalle de Site (origen, dominios, estado) |
| SP-0017 | SSO wp-admin (magic link + plugin spanel-sso) |
| SP-0018 | access logs (traefik → Spanel) |
| SP-0019 | file manager (filebrowser sidecar + forwardAuth) |

## 11. Decisiones tomadas

1. Traefik como proxy (labels docker + forwardAuth)
2. docker-mailserver sobre mailcow (liviano, 1 container)
3. 1 db por sitio WordPress
4. Server como entidad desde el día 1 (multi-host futuro)
5. SSO via magic link + plugin en WP, no passwords guardados
6. Filebrowser como sidecar, no file manager propio
