# A.SPEC 0026 — HTTPS interno con CA ACME local en Docker

> `risk: high` — emite certificados desde una CA propia y configura TLS en el
> proxy. Todo ocurre en el laboratorio delimitado por A.SPEC 0025: no toca el
> Traefik de producción ni `/opt/stacks`, no abre puertos públicos y no crea
> confianza global en el equipo del usuario sin un paso explícito.
>
> Esta versión **reemplaza** el borrador anterior de la 0026 ("prueba live
> contra el Traefik real"), que queda archivado en el historial de git para
> cuando exista un FQDN público real.

## WHY

Un dominio interno como `lucas.spanel` no puede recibir un certificado de
Let's Encrypt: `spanel` no es un TLD delegado y la CA pública no puede validar
el nombre. Sin embargo, sí se puede tener **HTTPS real** —protocolo ACME,
X.509 válido, cadena de confianza verificable— usando una CA ACME local
(`step-ca`) dentro de Docker y haciendo que Traefik obtenga el certificado con
el mismo flujo que usaría contra Let's Encrypt.

Sin esto, probar el CLI de dominios (A.SPEC 0025, entregable 2) obliga a usar
staging público, DNS real y el proxy de producción. Con una CA local, el ciclo
completo `add → certificado → HTTPS → remove` se puede ensayar tantas veces
como haga falta, sin Internet, sin rate limits y sin riesgo.

## WHAT

Levantar en Docker un laboratorio autosuficiente que emita certificados reales
por ACME para nombres internos y demostrar, con evidencia, que:

1. `lucas.spanel` obtiene un certificado emitido por la CA local mediante un
   intercambio ACME real, no un certificado autofirmado por router.
2. Traefik sirve `https://lucas.spanel` y redirige HTTP → HTTPS.
3. El certificado valida contra la raíz de la CA local (`openssl verify` y
   `curl --cacert`).
4. Agregar, consultar y quitar el dominio por CLI deja el sistema consistente
   e idempotente.
5. Reiniciar el laboratorio conserva la misma CA y los certificados emitidos.
6. Ni el Traefik real, ni `/opt/stacks`, ni los contenedores ajenos se tocan.

### Verdad explícita sobre "certificado real"

El certificado es **criptográficamente real y emitido por una CA real por
ACME**, pero **no es de confianza pública**: ningún navegador lo acepta sin
instalar antes la raíz de la CA local. Ese paso es manual y está documentado;
la A.SPEC no instala nada en el almacén de confianza del sistema.

## SCOPE

Entra:

- CA ACME local persistente en Docker (`step-ca`) con perfil ACME habilitado.
- DNS interno del laboratorio que resuelve los nombres gestionados.
- Resolver `internal` en el Traefik del laboratorio, con confianza explícita
  hacia la CA local.
- Perfil `internal` en `scripts/proxy_domain.py`: crear, listar, consultar y
  eliminar dominios internos.
- Exportación de la raíz pública para verificación del cliente.
- Pruebas y evidencia descritas abajo.

No entra:

- Let's Encrypt público, DNS público, wildcards o DNS-01.
- El Traefik de producción y cualquier archivo de `/opt/stacks`.
- Instalar la CA en el sistema operativo del usuario de forma automática.
- UI, base de datos y plugin `proxy` de la aplicación (A.SPEC 0027).

## CONTRACT

### Topología del laboratorio

Todos los recursos llevan `com.spanel.lab=true` y viven en la red
`spanel-traefik-lab` creada por A.SPEC 0025.

| Componente | Nombre | Rol |
|---|---|---|
| Proxy | `spanel-traefik-lab` | Termina TLS, resuelve certificados |
| CA ACME | `spanel-step-ca` | Emite certificados vía ACME |
| DNS interno | `spanel-lab-dns` | Resuelve los FQDN gestionados |

Puertos del host: `9080` HTTP, `9081` dashboard y HTTPS **automático**: `443`
si está libre, si no `9443`. Todos en `127.0.0.1`. El puerto de la CA **no** se
publica en el host. El DNS interno puede publicarse opcionalmente en
`127.0.0.1:5353`.

### CA local

- Imagen fijada (`smallstep/step-ca`), nunca `latest`.
- Inicialización no interactiva, con provisión ACME habilitada.
- Volumen propio y persistente; la raíz y su clave **nunca** se escriben en el
  repositorio ni en logs.
- URL ACME interna: `https://spanel-step-ca:9000/acme/acme/directory`.
- Se exporta únicamente el certificado **raíz público** a
  `scripts/traefik-lab/ca/root_ca.crt` (ignorado por git) para verificación.
- La huella SHA-256 de la raíz se registra en la evidencia.

### Resolver en Traefik

```yaml
certificatesResolvers:
  internal:
    acme:
      caServer: https://spanel-step-ca:9000/acme/acme/directory
      caCertificates:
        - /lab-ca/root_ca.crt
      email: lab@spanel.internal
      storage: /etc/traefik/data/acme.json
      tlsChallenge: {}
```

- `caCertificates` permite que Traefik confíe en la CA que sirve el endpoint
  ACME por HTTPS. No se usa `insecureSkipVerify`.
- Desafío `tls-alpn-01`: la CA valida contra el mismo Traefik que responde.
- El resolver público de staging de 0025 se conserva, pero deshabilitado por
  defecto en el perfil interno.

### DNS interno

- `spanel-lab-dns` sirve una zona local y responde los FQDN gestionados con la
  IP de `spanel-traefik-lab` en la red del laboratorio.
- `step-ca` usa ese DNS para resolver el nombre a validar.
- Agregar o quitar un dominio actualiza la zona **sin reiniciar** contenedores.
- No se modifica `/etc/hosts` del host ni la configuración DNS del sistema.

### CLI

`scripts/proxy_domain.py` (entregable 2 de 0025) implementa un perfil:

```bash
python3 scripts/proxy_domain.py doctor
python3 scripts/proxy_domain.py add \
  --site spanel-lucas-test-wp --domain lucas.spanel --dry-run
python3 scripts/proxy_domain.py add \
  --site spanel-lucas-test-wp --domain lucas.spanel --set-wordpress-url
python3 scripts/proxy_domain.py status --domain lucas.spanel
python3 scripts/proxy_domain.py list
python3 scripts/proxy_domain.py remove --domain lucas.spanel
```

El perfil por defecto es `internal`; el perfil público se pide explícitamente
con `--profile public` y no forma parte de esta entrega.

Reglas del perfil `internal`:

- Acepta nombres de una o más etiquetas con TLD interno (`spanel`), siempre en
  minúsculas, sin esquema, puerto, `*` ni `/`. El perfil público rechaza los
  TLD internos (`spanel`, `local`, `test`, `example`, etc.).
- No exige ni consulta DNS público; valida contra el DNS del laboratorio.
- Escribe `tls.certResolver: internal` en el YAML del sitio.
- `doctor` verifica: contenedores del laboratorio, resolver `internal` presente
  en la configuración estática, CA alcanzable, DNS interno respondiendo y
  confianza del cliente hacia la raíz.
- `status` determina emisor y `notAfter` por TLS, usando la raíz local como
  ancla; nunca lee `acme.json`.
- `--dry-run` no escribe, no pide certificado y no modifica DNS.
- Códigos de salida de 0025 se mantienen; un fallo de CA local usa `5`.
- `remove` elimina el YAML del sitio y la entrada DNS; no reinicia nada.
- Ningún secreto (clave de CA, tokens, contraseñas) aparece en argv, logs ni
  auditoría.
- Al terminar `add`, imprime el **link real** (`https://<dominio>` si el puerto
  HTTPS es 443, si no `https://<dominio>:<puerto>`), la línea de `/etc/hosts`
  para el host y el comando `curl` verificado con la CA local.
- `add` no declara éxito solo con el certificado: espera además a que la ruta
  responda por HTTPS con un código distinto de `404`/`503`, porque el file
  provider recarga el YAML con un pequeño retardo.
- `--set-wordpress-url` ejecuta `wordpress:cli` en un contenedor efímero que
  comparte la red del sitio y recibe el entorno (incluida la conexión a la BD)
  por un archivo temporal `0600`; nunca por `argv`, logs ni auditoría.

### Estado del certificado

Se considera `active` cuando, además de responder por TLS, la cadena valida
contra la raíz local y el `issuer` corresponde a la CA del laboratorio. Un
certificado autofirmado por Traefik (`TRAEFIK DEFAULT CERT`) es **fallo**, no
éxito.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: El laboratorio no contacta ni modifica el proxy de producción.
    proof: No se usan variables SPANEL_PROXY_* de producción en modo interno; prohibido escribir en /opt/stacks; `doctor` aborta si apunta al Traefik real.
  - id: I2
    statement: Solo se crean y destruyen recursos con com.spanel.lab=true.
    proof: `down` filtra por label; ningún comando global (rm/stop/prune).
  - id: I3
    statement: La clave privada de la CA nunca sale del volumen ni del host.
    proof: Solo se exporta root_ca.crt; .gitignore cubre scripts/traefik-lab/ca/ y data/; revisión de logs y argv.
  - id: I4
    statement: Un certificado interno nunca se confunde con uno de confianza pública.
    proof: `status` exige issuer de la CA local; la evidencia muestra el issuer y la raíz usada; la documentación advierte la instalación manual.
  - id: I5
    statement: Agregar o quitar un dominio no reinicia Traefik, la CA ni los sitios.
    proof: Comparar StartedAt de los tres contenedores antes y después; el file provider usa watch y el DNS se recarga sin reinicio.
  - id: I6
    statement: La operación es idempotente y sobrevive a un reinicio del laboratorio.
    proof: Repetir `add` produce el mismo YAML y el mismo certificado; `down`/`up` conserva raíz y certificados por volumen.
  - id: I7
    statement: Ningún YAML fuera de spanel-*.yml se modifica.
    proof: Escritor con prefijo y marcador `managed-by: spanel`; verificación de hashes de archivos ajenos.
```

## VERIFICATION

Los comandos se ejecutan en el host con Docker. En este entorno de desarrollo
(Android/Termux) no hay Docker local: la verificación se hace contra el Docker
del laptop/servidor y se adjunta la salida.

### T1 — Laboratorio arriba con CA

```bash
./scripts/traefik-lab.sh up
./scripts/traefik-lab.sh status
docker inspect spanel-step-ca --format '{{.State.Health.Status}}'
docker exec spanel-step-ca step certificate fingerprint /home/step/certs/root_ca.crt
```

Evidencia: `StartedAt` de los tres contenedores y huella de la raíz.

### T2 — Diagnóstico del perfil interno

```bash
python3 scripts/proxy_domain.py doctor
```

Debe reportar CA alcanzable, resolver `internal` presente, DNS interno
respondiendo y confianza del cliente. Sin Docker o con la CA caída, sale `4`.

### T3 — Dry-run

```bash
python3 scripts/proxy_domain.py add \
  --site spanel-lucas-test-wp --domain lucas.spanel --dry-run
```

Evidencia: YAML impreso con `certResolver: internal`, sin escrituras ni
certificado emitido.

### T4 — Alta y certificado

```bash
python3 scripts/proxy_domain.py add \
  --site spanel-lucas-test-wp --domain lucas.spanel
python3 scripts/proxy_domain.py status --domain lucas.spanel

# <puerto> es el que imprimió `add`: 443 si estaba libre, si no 9443.
echo "127.0.0.1 lucas.spanel" | sudo tee -a /etc/hosts
curl -sSI --cacert scripts/traefik-lab/ca/root_ca.crt https://lucas.spanel/
openssl s_client -connect 127.0.0.1:<puerto> -servername lucas.spanel \
  -CAfile scripts/traefik-lab/ca/root_ca.crt </dev/null 2>/dev/null \
  | openssl x509 -noout -issuer -subject -dates -ext subjectAltName
```

Criterios: `status` pasa de `pending_cert` a `active`; `curl` responde `200`
(o `301` desde `http://lucas.spanel:9080`); `issuer` es la CA del laboratorio;
el SAN incluye `lucas.spanel`; la verificación con `-CAfile` no reporta error;
el link impreso se abre en el navegador del host sin advertencias.

### T5 — Confianza del cliente

```bash
curl -sSI https://lucas.spanel/            # debe fallar sin --cacert
curl -sSI --cacert scripts/traefik-lab/ca/root_ca.crt https://lucas.spanel/
```

Documentar el paso manual para el navegador (importar `root_ca.crt` como
autoridad). No se automatiza.

### T6 — Idempotencia

```bash
sha256sum scripts/traefik-lab/dynamic/spanel-lucas.yml
python3 scripts/proxy_domain.py add \
  --site spanel-lucas-test-wp --domain lucas.spanel
sha256sum scripts/traefik-lab/dynamic/spanel-lucas.yml
```

Mismo hash; `StartedAt` sin cambios.

### T7 — Persistencia

```bash
./scripts/traefik-lab.sh down
./scripts/traefik-lab.sh up
python3 scripts/proxy_domain.py status --domain lucas.spanel
```

La huella de la raíz y el certificado emitido se conservan.

### T8 — Baja

```bash
python3 scripts/proxy_domain.py remove --domain lucas.spanel
```

El YAML del sitio desaparece (o queda sin dominios), la entrada DNS se retira y
el nombre deja de rutear por Spanel. El resto del laboratorio sigue igual.

### T9 — Aislamiento

```bash
docker ps --format '{{.Names}}\t{{.Label "com.spanel.lab"}}'
docker inspect traefik --format '{{.State.StartedAt}}'   # si existe en el host
ls -la /opt/stacks/traefik/config/                       # solo lectura
```

Un solo Traefik de laboratorio, ningún recurso nuevo sin label, `StartedAt` de
producción intacto y archivos ajenos con hash y fecha sin cambios.

### T10 — Purga

```bash
./scripts/traefik-lab.sh down --purge --yes
docker volume ls | grep spanel-    # sin resultados
```

## CRITERIOS DE ACEPTACIÓN

- `lucas.spanel` queda con HTTPS válido emitido por la CA local vía ACME.
- El link que imprime el CLI se abre en el navegador del host con candado,
  una vez agregada la línea de `/etc/hosts` e importada la CA local.
- `curl --cacert` valida la cadena; sin `--cacert` el certificado no es de
  confianza pública (comportamiento esperado y documentado).
- No se reinició Traefik, la CA ni los sitios al agregar o quitar el dominio.
- No se tocó producción, `/opt/stacks` ni archivos ajenos.
- `remove` deja el dominio sin ruta, sin DNS y sin YAML huérfano.
- La prueba es repetible de forma idempotente, también tras `down`/`up`.

## ROLLBACK

1. `./scripts/traefik-lab.sh down` detiene y elimina los contenedores y la red
   del laboratorio.
2. `./scripts/traefik-lab.sh down --purge` borra además volúmenes, CA y datos.
3. Si se importó la raíz en el navegador, quitarla manualmente del almacén de
   confianza.
4. Nada que revertir fuera del laboratorio: producción y `/opt/stacks` no se
   tocan en ningún paso.

## CHANGE SURFACE

```yaml
change_surface:
  allowed:
    - scripts/traefik-lab.sh
    - scripts/traefik-lab/**
    - scripts/proxy_domain.py
    - scripts/spanel_proxy/**
    - scripts/tests/test_proxy_domain.py
    - aspecs/0026-traefik-internal-acme-ca.md
  prohibited:
    - vendor/**
    - apps/**
    - plugins/**
    - .env
    - .env.docker
    - /opt/stacks/**
```

## BLAST RADIUS

```yaml
blast_radius:
  direct:
    - docker.local.lab
    - proxy.lab.config
    - proxy.lab.tls
  indirect:
    - scripts.proxy_domain
  must_not_affect:
    - traefik.production
    - traefik.existing.routes
    - containers.other
    - host.trust_store
    - dns.public
```

## COMPOSITION

```yaml
composition:
  requires_aspecs:
    - A.SPEC 0025
  must_compose_with:
    - scripts/traefik-lab.sh
  systemic_invariants:
    - No se toca producción
  composition_checks:
    - ./scripts/traefik-lab.sh down --purge deja el host como estaba
    - ./scripts/traefik-lab.sh up reproduce la misma CA
```

## FUERA DE ALCANCE

- Certificado de confianza pública y la prueba contra el Traefik real: queda
  para una A.SPEC futura cuando exista un FQDN público con DNS.
- UI, base de datos y plugin `proxy` (A.SPEC 0027).
- Wildcards, DNS-01 y automatización del proveedor DNS.
- Instalar la CA local en el sistema operativo o en el navegador por script.
- Migrar `siteurl` de sitios existentes o hacer search-replace de contenido.

## TRACEABILITY

- Requirement: HTTPS interno verificable para nombres no públicos
- owner: luc444s
- approver: luc444s
- Commit:
- Deployment: laboratorio Docker local
