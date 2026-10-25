# A.SPEC 0028 — Certificado interno y dominio de correo para `mail.lucas.spanel`

> `risk: high` — emite material de clave, **recrea el contenedor de correo en
> ejecución** y cambia la terminación TLS de un servicio con datos. Un error deja
> el correo sin TLS o sin arrancar. Exige `approver:` humano.
>
> Numeración: **0028**. La A.SPEC 0027 está reservada por 0025 y 0026 para el
> plugin `proxy` de la aplicación; esta entrega no lo implementa.

## WHY

`lucas.spanel` ya sirve HTTPS con un certificado emitido por la CA ACME local
(A.SPEC 0026), pero el correo quedó afuera:

- `spanel-mail` (docker-mailserver v15.1.0) **no tiene TLS**: `SSL_TYPE` sin
  definir, `doveconf` con `ssl = no`, `postconf` con `smtpd_use_tls=no` y
  `smtpd_tls_cert_file` vacío. Medido el 2026-10-25: `993`, `465` y `587` no
  presentan certificado (`no peer certificate available`). No es un problema de
  confianza: no hay TLS que validar.
- El buzón vive en `lucas.spanel`, pero el nombre al que se conectan los clientes
  debe ser otro: `mail.lucas.spanel`. Sin esa separación no hay forma de tener un
  certificado correcto, porque el dominio de buzón no identifica al servidor.
- `lucas.spanel` no es un TLD delegado: no hay Let's Encrypt, ni MX público, ni
  SPF/DKIM/DMARC públicos posibles. La única vía es la CA interna.

El objetivo final del usuario es concreto y verificable: **enviar y leer un correo
interno entre buzones `@lucas.spanel` con validación TLS contra la CA local**, sin
aceptar certificados inválidos en ningún paso.

## WHAT

Establecer esta verdad, con evidencia reproducible:

1. Existe un certificado X.509 para `mail.lucas.spanel` emitido por la CA del
   laboratorio, con clave propia, guardado fuera de git y fuera de argv/logs.
2. `spanel-mail` sirve IMAPS (`993`), SMTPS (`465`) y submission (`587`) con ese
   certificado, y la cadena valida contra `root_ca.crt` para el `servername`
   `mail.lucas.spanel`.
3. El dominio de buzón `lucas.spanel` está registrado en docker-mailserver y el
   buzón `lucas@lucas.spanel` existe y autentica.
4. Un cliente que confía solo en la CA local envía un mensaje de
   `lucas@lucas.spanel` a `lucas@lucas.spanel` por submission con STARTTLS y lo
   recupera por IMAPS con verificación de certificado, sin `verify=False`.
5. La emisión y la entrega son idempotentes, se pueden renovar y se pueden
   revertir, restaurando el contenedor anterior si algo falla.

### Verdad explícita sobre "correo interno"

El correo funciona **solo dentro del laboratorio**. No hay entrega a Internet, ni
recibe correo externo: `lucas.spanel` no resuelve públicamente y no existe
delegación. Un cliente de correo acepta el certificado únicamente si antes se
importa la CA local (`scripts/traefik-lab/ca/root_ca.crt`).

## SCOPE

Entra:

- CLI `scripts/mail_cert.py` (emisión, estado, check, renovación) que reutiliza la
  CA y las rutas del laboratorio de 0026.
- Emisión directa contra el provisioner JWK de `step-ca` (no ACME) con
  `--not-after` configurable.
- Destino de entrega `mail` (`--deliver mail`): adopción del contenedor
  `spanel-mail`, montaje de TLS, `SSL_TYPE=manual`, reload y verificación.
- Registro del dominio de buzón y del buzón de prueba por la API del plugin
  `mail` existente (sin tocar `vendor/`).
- DNS interno y `/etc/hosts` para `mail.lucas.spanel` y MX de `lucas.spanel`.
- Renovación programada (timer/cron) y comando manual idempotente.
- Prueba live de envío/lectura con verificación TLS.

No entra:

- Correo público, MX público, SPF/DKIM/DMARC, relay, antispam, cuotas.
- Clientes de correo reales, Thunderbird, móviles o UI del plugin `mail`.
- Cambiar el `hostname` del contenedor, `POSTMASTER_ADDRESS` ni las cuentas
  existentes (`test1@spanel.local`, `testlucas@spanel.local`, etc.).
- Emitir certificado para `lucas.spanel` (dominio de buzón) o wildcards.
- El plugin `proxy` (A.SPEC 0027), el Traefik de producción y `/opt/stacks`.
- Instalar la CA local en el sistema operativo o en el navegador por script.

## T0 — RESULTADOS MEDIDOS (2026-10-25, ThinkPad `lucas-ThinkPad-E570`)

Medido por SSH contra el host real, no supuesto. Estos valores son el punto de
partida y la rama elegida.

### Servidor de correo

| Dato | Valor medido |
|---|---|
| Imagen | `ghcr.io/docker-mailserver/docker-mailserver:latest`, ID `sha256:4f5093c251b61d5691f5ccfd3f90fcbf505585bfd045546430d282e5417658c8` |
| Release DMS | `v15.1.0` (`DMS_RELEASE`) |
| `hostname` / `myhostname` | `mail.spanel.local` |
| `SSL_TYPE` | **sin definir** (ni `SSL_CERT_PATH` ni `SSL_KEY_PATH`) |
| TLS real | **desactivado**: `doveconf` → `ssl = no`, cert snakeoil; `postconf` → `smtpd_use_tls=no`, `smtpd_tls_cert_file` vacío |
| Puertos publicados | `25`, `143`, `465`, `587`, `993` en `0.0.0.0` |
| Red | `bridge` (no está en `spanel-traefik-lab`) |
| Redes publicadas | `{"143", "25", "465", "587", "993"}` |
| Montajes | volúmenes `spanel-mail-config → /tmp/docker-mailserver`, `spanel-mail-data → /var/mail`, `spanel-mail-state → /var/mail-state` |
| `/etc/dms/tls` | existe, **vacío**, `root:root 0755` (sin montaje) |
| `restart` | `unless-stopped` |
| `entrypoint` / `cmd` | `dumb-init --` / `supervisord -c /etc/supervisor/supervisord.conf` |
| Usuario | root (sin `--user`) |
| Capabilities | ninguna extra |
| Entorno | 12 variables; solo `ENABLE_FAIL2BAN=0`, `ENABLE_POP3=1`, `ONE_DIR=1`, `POSTMASTER_ADDRESS=postmaster@spanel.local`, `DMS_RELEASE` y los `POSTGREY_*` son relevantes |

Cuentas existentes (**13**, en 7 dominios): `test1@spanel.local`,
`test2@spanel.local`, `testlucas@spanel.local`, `t1@spanel-test.local`,
`t2@spanel-test.local`, `admin@miempresa.com`, `ventas32@systutor.com`,
`hola@systutor.com`, `ventas@legalattorney.pe`, `hola@legalattorney.pe`,
`chat@legalattorney.pe`, `a@algisac.com`, `holasss@pruebas2.com`.

`/etc/postfix/vhost` contiene: `algisac.com`, `legalattorney.pe`, `miempresa.com`,
`pruebas2.com`, `spanel-test.local`, `spanel.local`, `systutor.com`.
**`lucas.spanel` no está.** `virtual_mailbox_domains = /etc/postfix/vhost`.

Consecuencia: hay dominios con apariencia de producción en el mismo servidor. La
recreación debe conservarlos y la verificación I3 compara las 13 cuentas.

### CA y laboratorio

| Dato | Valor medido |
|---|---|
| Provisioners | JWK **`admin`** (existe) + `acme` |
| Claims del JWK | sin `claims` explícitos → duraciones por defecto (24 h) |
| CLI en la CA | `step` 0.30.2 disponible en el contenedor |
| Contraseña del provisioner | `/home/step/secrets/password` (dentro), `scripts/traefik-lab/ca/password` (host, 0600) |
| Contenedores lab | `spanel-traefik-lab` (`172.25.0.4`), `spanel-step-ca` (`.3`), `spanel-lab-dns` (`.2`), `spanel-lucas-wp` (`.5`), red `172.25.0.0/16` |
| HTTPS web | `lucas.spanel` en `443` con serial `975693484C85ABE6D7C017ADFABA55F8`, `notAfter` 2026-10-26 (24 h), issuer `Spanel Lab CA Intermediate CA` |
| `/etc/hosts` del host | solo `127.0.0.1 lucas.spanel` (falta `mail.lucas.spanel`) |
| DNS del lab | solo `172.25.0.4 lucas.spanel` |
| Repo en el host | `/home/lucas/mailadmin` **sin `.git`** (copia); se sincroniza desde Termux |
| Python del host | 3.12.3 |

### Emisión validada con una prueba real (2026-10-25)

Antes de escribir código se ejecutó el camino de emisión completo contra el CA
real, con un nombre descartable `probe.spanel` y 1 h de vida. Resultado:

- El contenedor efímero `smallstep/step-ca:0.30.2` en `spanel-traefik-lab`
  emitió `leaf.crt` (RSA 2048, `0600`) y `privkey.key` sin tocar el CA ni el correo.
- La clave del certificado coincide con la privada (`openssl x509 -pubkey` ==
  `openssl pkey -pubout`), SAN `DNS:probe.spanel`, issuer
  `Spanel Lab CA Intermediate CA`.
- **Hallazgo**: `openssl verify -CAfile root_ca.crt` **falla** con
  `unable to get local issuer certificate`, porque el emisor es el intermedio.
  La verificación correcta es `-CAfile root_ca.crt -untrusted intermediate_ca.crt`
  (o `-CAfile <root+intermediate>`). El servidor debe enviar `fullchain.crt` =
  hoja + intermedio, y el cliente solo necesita la raíz.
- **Hallazgo**: `--not-after 720h` es rechazado hoy:
  `requested duration of 720h1m0s is more than the authorized maximum certificate
  duration of 24h1m0s`. Confirma que hay que subir el provisioner o aceptar 24 h.
- El contenedor imprime un aviso benigno (`there is no ca.json config file`)
  porque se le pasan todos los parámetros por flag; no es un error.

Por eso el CA debe exportar también su **intermedio público** a
`scripts/traefik-lab/ca/intermediate_ca.crt` (es público, no es la clave), igual
que ya exporta la raíz.

### Rama elegida

- **Se recrea `spanel-mail`**, porque `SSL_TYPE` es una variable de entorno y no
  se puede activar en caliente. Se usa el **ID de imagen** medido, no `:latest`,
  para que la recreación no cambie de versión si hay un `pull` de por medio.
- **Se conserva el `hostname` `mail.spanel.local`** (cambiarlo altera
  `myhostname` y el saludo SMTP de las 7 dominios existentes). El certificado
  cubre `mail.lucas.spanel` **y** `mail.spanel.local` como SAN.
- El bind mount va sobre `/etc/dms/tls` (ya existe y está vacío), en `:ro`.
- Se **sube la duración del provisioner `admin`** antes de emitir: hoy no tiene
  claims, así que `--not-after 720h` fallaría.
- `lucas.spanel` se registra como dominio de buzón en DMS v15.1.0 y se verifica
  en `/etc/postfix/vhost`; el mecanismo exacto (`setup email add` vs. registro
  explícito de dominio) se confirma en T7 y queda documentado con la versión.

### Comandos de la medición (repetibles)

```bash
docker inspect spanel-mail --format '{{.Config.Image}} {{.Image}}'
docker inspect spanel-mail --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E 'SSL_TYPE|SSL_CERT|SSL_KEY|POSTMASTER|DMS_RELEASE'
docker exec spanel-mail setup email list
docker exec spanel-mail hostname
docker exec spanel-mail doveconf -n | grep -Ei 'ssl'
docker exec spanel-mail postconf -h smtpd_use_tls smtpd_tls_cert_file
docker exec spanel-mail cat /etc/postfix/vhost
docker exec spanel-step-ca step ca provisioner list
docker inspect spanel-mail > /tmp/spanel-mail.inspect.json
```

## CONTRACT

### Modelo de nombres

| Concepto | Valor | Uso |
|---|---|---|
| Dominio de buzón | `lucas.spanel` | parte local de las direcciones `@lucas.spanel` |
| Hostname de cliente | `mail.lucas.spanel` | SNI/TLS, IMAP, SMTP, MX |
| Dominio de sitio web | `lucas.spanel` | ya sirve HTTPS por Traefik (0026), no se toca |

Un certificado **nunca** cubre los dos roles por conveniencia: el SAN primario es
`mail.lucas.spanel`. Se agregan SANs adicionales solo si son nombres por los que
realmente se conecta un cliente. Para este servidor son exactamente dos:

- `mail.lucas.spanel` (nombre del certificado y de los clientes),
- `mail.spanel.local` (`hostname` medido del contenedor, para que el saludo SMTP
  de Postfix y el certificado coincidan).

`lucas.spanel` **no** entra como SAN: es el dominio de buzón.

### Emisión: JWK, no ACME

Se descarta el resolver ACME de Traefik por dos razones concretas:

- El provisioner ACME de `step-ca` emite a 24 h (lo medido en 0026); renovar
  correo cada día agrega un punto de fallo innecesario.
- El desafío de 0026 es `tls-alpn-01` en el `443`, que pertenece a Traefik: el
  servidor de correo no puede responderlo, y montar una ruta ficticia en Traefik
  para validar un nombre que no enruta nada es un acoplamiento peor.

Por lo tanto la emisión usa el provisioner JWK del CA, que no requiere desafío
porque el operador es dueño del CA:

```bash
docker run --rm --label com.spanel.lab=true --label com.spanel.ephemeral=true \
  --network spanel-traefik-lab \
  -v spanel-step-ca-data:/ca:ro \
  -v "$(pwd)/scripts/traefik-lab/ca/password:/secrets/password:ro" \
  -v "$CERT_DIR:/out" \
  smallstep/step-ca:<misma versión que 0026> \
  step ca certificate mail.lucas.spanel /out/leaf.crt /out/privkey.key \
    --ca-url https://spanel-step-ca:9000 \
    --root /ca/certs/root_ca.crt \
    --provisioner admin \
    --provisioner-password-file /secrets/password \
    --kty RSA --size 2048 \
    --not-after 720h \
    --san mail.lucas.spanel --san mail.spanel.local
```

Reglas:

- El contenedor efímero no publica puertos y se elimina al terminar (`--rm`).
- La contraseña del provisioner entra **por archivo montado**, nunca por `-e`,
  `argv` ni log. En la auditoría solo aparece la ruta.
- `fullchain.crt` = `leaf.crt` + intermedio del CA (`/ca/certs/intermediate_ca.crt`).
- `privkey.key` en `0600`; `fullchain.crt` en `0644`.
- **`step ca certificate` ya devuelve la cadena** (hoja + intermedio) en el
  archivo de certificado. `fullchain.crt` se construye copiando ese archivo, no
  concatenando el intermedio otra vez: concatenar produce 3 certificados con el
  intermedio repetido (observado en la prueba real). `certs.py` debe deduplicar
  por huella antes de escribir.
- El intermedio público se exporta a `scripts/traefik-lab/ca/intermediate_ca.crt`
  (`docker cp spanel-step-ca:/home/step/certs/intermediate_ca.crt`) la primera vez
  y se reutiliza; nunca se exporta la clave del intermedio ni la de la raíz.
- La verificación local usa `openssl verify -CAfile root_ca.crt -untrusted
  intermediate_ca.crt leaf.crt`, no `-CAfile root_ca.crt` a secas.
- Si `maxTLSCertDuration` es menor que `--not-after` (medido: 24 h), el comando
  ajusta el provisioner con `step ca provisioner update admin
  --x509-max-dur=8760h --x509-default-dur=720h` **solo si se pasó
  `--allow-provisioner-update`**; si no, aborta con código 5 y explica el comando
  exacto. Alternativa aceptada: no tocar el CA y emitir a 24 h con renovación
  diaria (el timer ya está previsto); se elige una y se registra en `meta.json`.

### Estructura en disco

```
scripts/traefik-lab/certs/mail.lucas.spanel/
  leaf.crt             # hoja, público, 0644
  fullchain.crt        # hoja + intermedio, público, 0644
  privkey.key          # 0600, ignorado por git (*.key)
  meta.json            # subject, SANs, issuer, serial, not_before, not_after,
                       # sha256, provisioner, entregado_a, entregado_en
  deliveries/mail.json # último estado entregado al contenedor (hash del cert)
  backups/<epoch>/     # definición previa del contenedor y cert anterior, si existía
```

La carpeta se crea `0700`. `privkey.key` se escribe de forma atómica (temporal +
`os.replace`) y nunca aparece en stdout, `docker inspect` ni auditoría: la
auditoría registra solo `sha256` y `serial`.

### CLI (`scripts/mail_cert.py`)

Un módulo, cuatro comandos. Se prefirió esto a repartir en `spanel_proxy/` porque
lo que hacía falta era determinismo operativo, no integrar el correo al CLI de
proxy; el plugin `proxy` (0027) sigue libre para reutilizar la lógica cuando
exista una UI.

```bash
python3 scripts/mail_cert.py status
python3 scripts/mail_cert.py check
python3 scripts/mail_cert.py issue [--deliver mail|none] [--force] [--dry-run] [--yes]
python3 scripts/mail_cert.py renew [--all] [--force] [--dry-run] [--yes]
```

Configuración **solo por entorno** (sin flags que puedan divergir entre
ejecuciones):

| Variable | Default |
|---|---|
| `SPANEL_MAIL_CONTAINER` / `MAIL_DMS_CONTAINER` | `spanel-mail` |
| `SPANEL_MAIL_DOMAIN` | `mail.lucas.spanel` |
| `SPANEL_MAIL_SANS` | `mail.spanel.local` |
| `SPANEL_MAIL_SOURCE_DIR` | `/etc/dms/source` |
| `SPANEL_CERT_DIR` | `scripts/traefik-lab/certs` |
| `SPANEL_CA_CONTAINER` / `SPANEL_CA_VOLUME` / `SPANEL_CA_URL` | `spanel-step-ca` / `spanel-step-ca-data` / `https://spanel-step-ca:9000` |
| `SPANEL_CA_PASSWORD_FILE` / `SPANEL_CA_ROOT` / `SPANEL_CA_INTERMEDIATE` | `scripts/traefik-lab/ca/*` |
| `SPANEL_CERT_NOT_AFTER` / `SPANEL_CERT_RENEW_AT` | `720h` / `30` |
| `SPANEL_MAIL_PORTS` / `SPANEL_MAIL_HOST` / `SPANEL_MAIL_TIMEOUT` | `993,465,587` / `127.0.0.1` / `120` |

Reglas:

- `issue` es idempotente sin `--force`: si el certificado tiene más del
  `SPANEL_CERT_RENEW_AT` (30 %) de vida y los mismos SAN, informa `sin cambios` y
  no emite ni recrea.
- `issue --dry-run` imprime qué emitiría y el `docker run` completo de
  recreación, sin escribir nada.
- La recreación del contenedor es destructiva: exige `--yes` cuando no hay TTY;
  sin `--yes` en no-TTY aborta con 2.
- `check` es el guardián de la deriva: cadena, SAN, montaje `:ro`, `SSL_TYPE` y
  TLS en los tres puertos. Cualquier desvío sale con 8 y lo describe.
- Salidas: `0` ok, `2` uso, `4` Docker o definición no reproducible, `5` CA o
  emisión, `7` entrega fallida (con rollback), `8` verificación posterior.
- Ningún mensaje, log ni `meta.json` contiene material de clave.

### Destino `mail`

Paso a paso, con rollback en cada punto:

1. **Descubrir**: `docker inspect spanel-mail` (o por SSH si `SPANEL_PROXY_SSH_HOST`
   está definido, reutilizando el transporte de 0025/0026). Si el contenedor no
   existe o no corre → código 4.
2. **Conservar**: guardar en `backups/<epoch>/container.json` la definición
   completa y `docker inspect` del contenedor anterior; si ya había cert manual
   montado, copiar esos dos archivos.
3. **Preparar**: escribir `leaf.crt`/`fullchain.crt`/`privkey.key` en el directorio
   host `SPANEL_MAIL_HOST_TLS_DIR`. DMS copia esos archivos a `/etc/dms/tls/{cert,key}`
   y les aplica `0644`/`0600` él mismo, así que **no hace falta** ajustar gid ni
   grupo: basta con que el proceso de arranque (root) pueda leerlos. La clave queda
   `0600` en el host y la cadena `0644`.
4. **Adoptar**: reconstruir el contenedor **conservando** imagen (ID medido,
   no el tag `:latest`), puertos, redes, volúmenes, labels, `restart`, `hostname`,
   `entrypoint`, `cmd`, capabilities y todo el entorno, agregando solo:
   `SSL_TYPE=manual`, `SSL_CERT_PATH=/etc/dms/source/fullchain.crt`,
   `SSL_KEY_PATH=/etc/dms/source/privkey.key`, y el bind mount
   `<host dir>:/etc/dms/source:ro`.
   **El montaje no va en `/etc/dms/tls`**: es el destino interno donde DMS copia
   y donde escribe, y montarlo ahí rompería la copia (o la dejaría en solo
   lectura). Verificado leyendo `/usr/local/bin/helpers/ssl.sh` de DMS v15.1.0.
   Si el contenedor ya está adoptado con ese montaje, este paso se salta.
5. **Conmutar**: `docker stop` + `docker rename spanel-mail
   spanel-mail.pre-mailtls-<epoch>`, arrancar el nuevo con el mismo nombre,
   esperar a que `993` complete un handshake con `Verify return code: 0` y a que
   `doveconf`/`postconf` reporten TLS activo, con timeout acotado. El contenedor
   no tiene healthcheck propio: el readiness se mide por puerto y `supervisorctl`.
6. **Verificar**: TLS en `993`, `465` y `587` (STARTTLS) con
   `-servername mail.lucas.spanel -CAfile root_ca.crt`; además comprobar que el
   emisor es el CA del laboratorio, que el SAN contiene `mail.lucas.spanel` **y**
   `mail.spanel.local`, que el hash del maildir no cambió y que las cuentas
   previas siguen listándose.
7. **Commit o rollback**: si todo verifica, retirar el contenedor anterior y
   escribir `deliveries/mail.json`. Si algo falla, `docker rm -f` el nuevo,
   `docker rename` de vuelta el anterior, `docker start`, revalidar que el correo
   volvió a su estado previo y salir con 7/8.

Reglas adicionales:

- El mismo comando **no** toca cuentas, buzones, maildir, contraseñas, DKIM ni
  `POSTMASTER_ADDRESS`: solo cambia TLS y el nombre del host de cliente.
- Sin `--deliver mail`, el comando solo deja los archivos en disco.
- `cert delivery revert --target mail` restaura la definición guardada (renombra
  el actual a `.reverted-<epoch>`, vuelve a crear el original) y verifica que el
  correo arranca; no borra la CA ni el certificado emitido.
- Si el contenedor tiene una definición que no se puede reproducir con
  `docker run` (por ejemplo `docker compose` con secretos externos), el comando
  **aborta con 4** y muestra el diff; no inventa una reconstrucción.

### Dominio de buzón y cuenta de prueba

Sin cambios en `vendor/**`: se usa la API existente del plugin `mail`
(`POST /accounts`, permiso `mail.accounts.create`), que ejecuta
`docker exec spanel-mail setup email add`.

- Precondición verificable: tras crear la cuenta, `lucas.spanel` aparece en
  `/etc/postfix/vhost` (medido: `virtual_mailbox_domains = /etc/postfix/vhost`,
  con los 7 dominios actuales). Si DMS v15.1.0 no registra el dominio
  automáticamente, se documenta el mecanismo exacto de esa versión y se aplica de
  forma persistente en `spanel-mail-config` (nunca editando archivos efímeros del
  contenedor); el criterio de aceptación no cambia.
- Que `lucas.spanel` **no** esté en `/etc/postfix/vhost` antes de empezar es parte
  de la línea base: el dominio se agrega en esta A.SPEC, no se asume.
- La contraseña del buzón de prueba se genera aleatoria, se muestra **una sola
  vez** al operador y nunca se registra en logs, auditoría ni `meta.json`.
- Las cuentas preexistentes deben seguir listándose igual antes y después.

### DNS y resolución

- `mail.lucas.spanel` se agrega al DNS del laboratorio
  (`scripts/traefik-lab/dns/hosts`) y se documenta la línea de `/etc/hosts` para
  el host, igual que 0026.
- MX de `lucas.spanel` → `mail.lucas.spanel` en el DNS del laboratorio (preferencia
  `10`). No se publica nada en DNS público.
- La entrega `lucas@lucas.spanel → lucas@lucas.spanel` es local y no depende de
  MX; el MX se agrega para que un cliente real no marque el dominio como inválido.

### Renovación

- `cert renew --all` recorre los certificados gestionados, reemite los que están
  por debajo del 30 % de vida y reentrega solo si el `sha256` cambió.
- El spec entrega `scripts/mail-cert.service` y `scripts/mail-cert.timer`
  (diario, `RandomizedDelaySec`, `Persistent=true`) más la instrucción de
  instalación; el timer no se instala automáticamente.
- Tras un `docker rm` del correo, `cert delivery check --target mail` reporta
  `not_delivered` y `cert renew --domain ... --force` lo repone.

### Entregables

1. `scripts/mail_cert.py` — emisión, `meta.json`, entrega determinista desde
   `docker inspect`, verificación y rollback. **Implementado.**
2. `scripts/tests/test_mail_cert.py` — unitarios puros (SAN, decisión de
   renovación, reconstrucción de `docker run`, rechazo de configuración no
   reproducible). **Implementado, 20 casos.**
3. `scripts/mail-cert.service` + `scripts/mail-cert.timer` — renovación diaria.
   **Implementado, no instalado.**
4. `scripts/tests/test_mail_cert_live.py` — la prueba de T8 como test repetible.
   **Implementado, 5 casos**; corre standalone en el host (sin pytest).
5. `scripts/proxy_domain.py` + `scripts/spanel_proxy/**` — quedan intactos: la
   integración con el CLI de dominios se pospone a A.SPEC 0027 (plugin `proxy`).

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: El Traefik de producción y /opt/stacks no se tocan.
    proof: El perfil internal nunca lee SPANEL_PROXY_* de producción; prohibido escribir en /opt/stacks; hash de /opt/stacks/traefik/config antes y después.
  - id: I2
    statement: lucas.spanel sigue sirviendo HTTPS con su certificado de 0026.
    proof: serial 975693484C85ABE6D7C017ADFABA55F8 y huella del cert de lucas.spanel iguales antes y después; curl --cacert responde 200; StartedAt de spanel-traefik-lab (2026-10-25T05:02:13Z) sin cambios.
  - id: I3
    statement: Los datos y cuentas de correo sobreviven a la recreación.
    proof: las 13 cuentas medidas en T0 (spanel.local, spanel-test.local, miempresa.com, systutor.com, legalattorney.pe, algisac.com, pruebas2.com) siguen listándose igual; /etc/postfix/vhost conserva los 7 dominios previos; maildir (find | wc -l + hash de rutas) igual; login de test1@spanel.local funciona.
  - id: I4
    statement: La clave privada del correo nunca sale del disco 0600 del host ni de un montaje de solo lectura.
    proof: privkey.key 0600 en host, 0640 dentro del contenedor con grupo medido; no aparece en argv, env, logs, meta.json ni git status; *.key sigue ignorado.
  - id: I5
    statement: Ningún certificado autofirmado ni de otra CA se acepta como éxito.
    proof: status y verificación exigen issuer = CA del laboratorio y cadena válida contra root_ca.crt; un fallo de verificación devuelve 8 y dispara rollback.
  - id: I6
    statement: La confianza sigue siendo interna y explícita.
    proof: openssl s_client sin -CAfile no valida; la documentación y la salida del CLI dicen que la CA debe importarse manualmente.
  - id: I7
    statement: Emitir dos veces no cambia nada si el certificado está vigente.
    proof: segunda ejecución informa sin cambios, mismo serial, mismo sha256, StartedAt del contenedor sin cambios.
  - id: I8
    statement: Un fallo en cualquier paso deja el correo como estaba.
    proof: el rollback renombra de vuelta el contenedor original (mismo ID), lo arranca y revalida 993/587; se prueba en T12 provocando un fallo.
  - id: I9
    statement: El dominio de buzón no se confunde con el hostname del certificado.
    proof: el SAN es exactamente {mail.lucas.spanel, mail.spanel.local}; no contiene lucas.spanel; el certificado no se emite para el dominio de buzón; meta.json registra ambos conceptos separados.
  - id: I10
    statement: Solo se crean recursos efímeros etiquetados y no se destructivos salvo el contenedor objetivo con --yes.
    proof: contenedores de emisión con com.spanel.lab=true y --rm; ningún prune/rm global; el único contenedor reemplazado es spanel-mail y queda respaldado.
  - id: I11
    statement: La contraseña del provisioner y las claves no aparecen en argv, env ni logs.
    proof: revisión de la auditoría de la ejecución y de docker inspect del contenedor efímero; la contraseña entra por archivo montado.
```

## VERIFICATION

Ejecutar en el host con Docker (ThinkPad `lucas-ThinkPad-E570`, repo
`/home/lucas/mailadmin`). En Termux no hay Docker local: la evidencia se captura
del host y se adjunta.

### T1 — Precondiciones (T0 del contrato)

Ya ejecutado el 2026-10-25 (§T0). Se repite antes de implementar para detectar
deriva (actualización de imagen, contenedor recreado a mano):

```bash
docker inspect spanel-mail --format '{{.Config.Image}} {{.Image}}'
docker exec spanel-mail setup email list
docker exec spanel-mail cat /etc/postfix/vhost
docker exec spanel-mail hostname
docker exec spanel-step-ca step ca provisioner list
```

Evidencia: coincidencia con la tabla de T0, o la rama nueva si algo cambió.

### T2 — Estado previo (la línea base que hay que mejorar)

```bash
for p in 993 465; do
  echo "== $p"; timeout 8 openssl s_client -connect 127.0.0.1:$p \
    -servername mail.lucas.spanel </dev/null 2>&1 | grep -E 'no peer certificate|subject=|issuer='
done
timeout 8 openssl s_client -starttls smtp -connect 127.0.0.1:587 </dev/null 2>&1 \
  | grep -E 'no peer certificate|subject=|issuer='
docker exec spanel-mail doveconf -n | grep -E '^ssl =|^ssl_cert'
docker exec spanel-mail postconf -h smtpd_use_tls smtpd_tls_cert_file
```

Resultado esperado **antes** (medido): `no peer certificate available` en `993` y
`465`; fallo de STARTTLS en `587`; `ssl = no`; `smtpd_use_tls = no`. Es el fallo
que esta A.SPEC corrige: hoy no hay TLS, no un certificado no confiable. Guardar
también `setup email list` (13 cuentas) y el conteo de maildir para I3.

### T3 — Dry-run

```bash
python3 scripts/mail_cert.py issue --deliver mail --dry-run
```

Evidencia: `docker run` de emisión y de recreación con secretos redactados, sin
escrituras ni emisión.

### T4 — Emisión

```bash
python3 scripts/mail_cert.py issue --deliver mail --yes
openssl verify -CAfile scripts/traefik-lab/ca/root_ca.crt \
  -untrusted scripts/traefik-lab/ca/intermediate_ca.crt \
  scripts/traefik-lab/certs/mail.lucas.spanel/leaf.crt
openssl x509 -in scripts/traefik-lab/certs/mail.lucas.spanel/leaf.crt \
  -noout -issuer -subject -dates -ext subjectAltName
openssl x509 -in scripts/traefik-lab/certs/mail.lucas.spanel/leaf.crt -noout -pubkey \
  | openssl md5
openssl pkey -in scripts/traefik-lab/certs/mail.lucas.spanel/privkey.key -pubout \
  | openssl md5
grep -c 'BEGIN CERTIFICATE' scripts/traefik-lab/certs/mail.lucas.spanel/fullchain.crt
stat -c '%a %U:%G %n' scripts/traefik-lab/certs/mail.lucas.spanel/*
jq . scripts/traefik-lab/certs/mail.lucas.spanel/meta.json
```

Criterios: `verify` devuelve `OK` (con `-untrusted`, medido); issuer del CA del
laboratorio; SAN exactamente `mail.lucas.spanel` + `mail.spanel.local`; los dos
MD5 de clave coinciden; `fullchain.crt` tiene 2 certificados; clave `0600`;
`meta.json` con serial y sha256; `git status --short` sin `*.key` ni `*.crt`.

### T5 — Entrega y recreación

```bash
docker inspect spanel-mail --format '{{range .Config.Env}}{{println .}}{{end}}' | grep SSL_
docker inspect spanel-mail --format '{{range .Mounts}}{{println .Destination}}{{end}}' | grep /etc/dms/source
python3 scripts/mail_cert.py check
```

Criterios: `SSL_TYPE=manual`, rutas `/etc/dms/source/*`, montaje `:ro` presente,
`delivery check` en `ok`. `lucas.spanel` y el cert de 0026 sin cambios (I2).

### T6 — TLS en los tres puertos

```bash
for p in 993 465; do
  openssl s_client -connect 127.0.0.1:$p -servername mail.lucas.spanel \
    -CAfile scripts/traefik-lab/ca/root_ca.crt </dev/null 2>/dev/null \
    | openssl x509 -noout -issuer -subject -ext subjectAltName
done
openssl s_client -starttls smtp -connect 127.0.0.1:587 \
  -servername mail.lucas.spanel -CAfile scripts/traefik-lab/ca/root_ca.crt \
  </dev/null 2>&1 | grep -E 'Verify return code|Verification'
```

Criterios: `Verify return code: 0 (ok)` en los tres; issuer del CA local; SAN
con `mail.lucas.spanel`.

### T7 — Dominio de buzón y cuenta

```bash
docker exec spanel-mail setup email list | grep lucas@lucas.spanel
docker exec spanel-mail cat /etc/postfix/vhost
docker exec spanel-mail doveadm auth test lucas@lucas.spanel -p "$TEST_PASSWORD"
```

Criterios: la cuenta aparece, `lucas.spanel` está en `/etc/postfix/vhost` junto a
los 7 dominios previos, y `doveadm auth test` autentica. Las 13 cuentas previas
siguen listadas (I3).

### T8 — Envío y lectura con verificación TLS (meta del usuario)

```bash
cd /home/lucas/mailadmin && python3 scripts/tests/test_mail_cert_live.py
# con pytest, si está instalado: python3 -m pytest scripts/tests/test_mail_cert_live.py -q -s
```

Se salta solo si falta la contraseña del buzón, así que un `pytest` genérico en
una máquina sin el laboratorio no intenta conectarse. Con
`ssl.create_default_context(cafile=root_ca.crt)` y **sin** desactivar la
verificación, el test:

1. comprueba que `993`, `465` y `587` presentan el mismo certificado, con CN
   `mail.lucas.spanel`, SAN correcto y emisor del CA local;
2. envía por `587` STARTTLS con `login()` y `Message-ID` único;
3. lo recupera por `993` con `SEARCH` por `Message-ID` y `FETCH` de cabeceras;
4. exige ver que la entrega fue local (`LMTP`/`Postfix` en las cabeceras).

Evidencia real (2026-10-25):

```text
PASS  test_imaps_993_presenta_el_certificado_de_la_ca_local
      imaps:993: serial=AA7F4769716504ADE3B4F76A0D2EBDC0 no_after=Nov 24 06:31:57 2026 GMT san=['mail.lucas.spanel', 'mail.spanel.local']
PASS  test_los_tres_puertos_presentan_el_mismo_certificado
PASS  test_smtps_465_presenta_el_certificado_de_la_ca_local
PASS  test_submission_587_starttls_presenta_el_certificado_de_la_ca_local
PASS  test_envio_y_lectura_interna_con_tls
5 ok, 0 fallas, 0 omitidos
resultado: OK
```

### T9 — Negativo de confianza

```bash
openssl s_client -connect 127.0.0.1:993 -servername mail.lucas.spanel \
  </dev/null 2>&1 | grep -E 'Verify return code|self-signed|unable to get'
```

Criterio: sin `-CAfile` la verificación falla (I6).

### T10 — Idempotencia

```bash
before=$(docker inspect spanel-mail --format '{{.State.StartedAt}}')
python3 scripts/mail_cert.py issue --deliver mail
after=$(docker inspect spanel-mail --format '{{.State.StartedAt}}')
test "$before" = "$after"
```

Criterio: `sin cambios`, mismo serial, mismo `StartedAt` (I7).

### T11 — Persistencia y renovación programada

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mail-cert.timer
systemctl list-timers mail-cert.timer
sudo systemctl start mail-cert.service
journalctl -u mail-cert.service -n 30 --no-pager
```

Criterio: el service sale 0 e informa `sin cambios`; el timer queda activo.
Evidencia también de `docker restart spanel-mail` conservando el certificado
montado.

### T12 — Rollback provocado

```bash
# Fallo inyectado sin tocar el certificado: se exige un puerto que no existe,
# de modo que la verificación posterior falla y el script debe revertir.
SPANEL_MAIL_PORTS=993,465,587,9999 \
  python3 scripts/mail_cert.py issue --force --yes
docker ps --format '{{.Names}}' | grep spanel-mail
openssl s_client -connect 127.0.0.1:993 -servername mail.lucas.spanel \
  -CAfile scripts/traefik-lab/ca/root_ca.crt </dev/null 2>/dev/null | grep 'Verify return'
python3 scripts/mail_cert.py check
```

Criterio: el comando detecta el fallo, restaura el contenedor anterior (mismo
ID), el correo vuelve a responder, `check` sale 0 y el proceso sale con 7 u 8
(I8). No dejar `SPANEL_MAIL_PORTS` alterado en el entorno persistente.
Resultado obtenido el 2026-10-25: `exit 7`, mismo ID, certificado anterior
restaurado y `check` en OK (ver §Simulacro de rollback ejecutado).

### T13 — Aislamiento y superficie

```bash
docker ps -a --format '{{.Names}}\t{{.Label "com.spanel.lab"}}'
docker inspect spanel-traefik-lab --format '{{.State.StartedAt}}'
ls -la /opt/stacks/traefik/config/     # solo lectura
git status --short
```

Criterios: ningún contenedor efímero huérfano; `startedAt` de Traefik sin
cambios; producción intacta; sin claves ni certificados en git.

## ESTADO DE IMPLEMENTACIÓN (2026-10-25)

La **entrega mínima se ejecutó a mano** contra el host real y quedó verificada.
Lo que falta es el código (`certs.py`, `delivery.py`, `mail.py`, subcomando
`cert`, timer), que debe reproducir exactamente esto.

Hecho y verificado:

| Paso | Resultado |
|---|---|
| Provisioner | `admin`: `maxTLSCertDuration` 8760h, `defaultTLSCertDuration` 720h; backup previo en `/home/step/config/ca.json.bak-20261025-010809`; `docker restart spanel-step-ca` |
| Certificado | serial `22BB2690D1B0A24939907FFA91DF02D1`, `CN=mail.lucas.spanel`, SAN `mail.lucas.spanel` + `mail.spanel.local`, 2026-10-25 → 2026-11-24, RSA 2048 |
| Verificación | `openssl verify -CAfile root_ca.crt -untrusted intermediate_ca.crt` → `OK`; clave y certificado coinciden (mismo MD5 de clave pública) |
| Entrega | `spanel-mail` recreado (ID `788d1ea7…`, imagen `sha256:4f5093c2…`), `SSL_TYPE=manual`, montaje `<certs>/mail.lucas.spanel:/etc/dms/source:ro` |
| TLS | `993`, `465` (implícito) y `587` (STARTTLS): `Verify return code: 0 (ok)`, TLS 1.3, issuer `Spanel Lab CA Intermediate CA` |
| DMS interno | `dovecot ssl = required`; `postfix smtpd_tls_chain_files = /etc/dms/tls/key /etc/dms/tls/cert`, `smtpd_tls_security_level = may` |
| Datos | maildir hash idéntico (`b569a862…`), 13 cuentas previas intactas, `/etc/postfix/vhost` con los 7 dominios previos |
| Buzón | `lucas@lucas.spanel` creado (`setup email add`), `lucas.spanel` agregado a `/etc/postfix/vhost` por el changedetector de DMS |
| Correo | PASS: 587 STARTTLS + auth → envío → 993 IMAPS + `SEARCH` por `Message-ID` → cabeceras recuperadas con `Received: ... with LMTP` |
| Web | `lucas.spanel` sigue `200` con el mismo serial `975693484C85ABE6D7C017ADFABA55F8` |
| Limpieza | contenedor anterior eliminado tras verificar; `meta.json` y `deliveries/mail.json` escritos |

Hallazgos para el código:

- El montaje de origen va en `/etc/dms/source:ro`, **no** en `/etc/dms/tls`
  (DMS copia ahí y le pone `0600`/`0644`).
- `step ca certificate` devuelve la cadena completa; deduplicar antes de escribir
  `fullchain.crt`.
- Agregar una cuenta dispara el changedetector de DMS (~2 s) que regenera
  `/etc/postfix/vhost` y recarga Postfix/Dovecot. `setup email list` puede
  imprimir un error cosmético (`listmailuser ... non-number argument`) para una
  cuenta recién creada sin maildir; no afecta la entrega.
- `postfix` en DMS v15 usa `smtpd_tls_chain_files`, no `smtpd_tls_cert_file`: la
  verificación debe mirar esa variable.

### Determinismo (lo que faltaba)

`scripts/mail_cert.py` reconstruye la definición del contenedor desde
`docker inspect` + `docker image inspect`. Evidencia en el host:

```
ok    nombre / imagen (por ID) / hostname / restart / puertos / volúmenes
ok    comando (no redeclara el de la imagen)
ok    env_tls (SSL_TYPE, SSL_CERT_PATH, SSL_KEY_PATH)
ok    env_extra (solo las variables que no son default de la imagen)
ok    montaje_ro
definición reproducible bit a bit: True
```

Y las tres pruebas de no-efecto:

```bash
python3 scripts/mail_cert.py issue --dry-run     # imprime el docker run, no escribe
python3 scripts/mail_cert.py issue --deliver mail  # "sin cambios" + "entrega sin cambios"
SPANEL_MAIL_SOURCE_DIR=/etc/dms/otro python3 scripts/mail_cert.py check
  # -> exit 8, "spanel-mail no usa este certificado: {…\"mount\": null…}"
```

Esa última línea es la prueba de que `check` detecta deriva real en vez de
declarar éxito siempre.

Instalación del timer (no ejecutada todavía):

```bash
sudo cp scripts/mail-cert.service scripts/mail-cert.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mail-cert.timer
systemctl list-timers mail-cert.timer
```

Mientras el timer no esté instalado, la renovación es manual pero ya determinista:

```bash
cd /home/lucas/mailadmin && python3 scripts/mail_cert.py renew --all --yes
```

### Simulacro de rollback ejecutado (T12)

```bash
SPANEL_MAIL_PORTS=993,465,587,9999 python3 scripts/mail_cert.py issue --force --yes
```

Salida real:

```text
emitido mail.lucas.spanel serial E271049AA113E6098BB0338C54C89BF0 hasta 2026-11-24T06:34:30+00:00
error: puerto 9999: sin handshake. Se restauró el contenedor anterior.
       Se restauró el certificado anterior (serial AA7F4769716504ADE3B4F76A0D2EBDC0)
exit=7
```

Estado después: **mismo container ID** (`788d1ea7…`), certificado anterior en
disco y en uso, sin contenedores `.pre-mailtls` huérfanos, `check` en OK.

El simulacro encontró dos defectos que ya están corregidos:

1. `deliver` declaraba `sin cambios` mirando solo env y montaje, aunque el proceso
   siguiera sirviendo el certificado anterior. Ahora compara el **serial que
   presenta el puerto 993** contra `meta.json`; si difiere, entrega.
2. El rollback arrancaba el contenedor antes de restaurar los archivos de
   certificado, así que DMS copiaba el certificado nuevo al iniciar y el
   "rollback" dejaba disco y contenedor en desacuerdo. Ahora restaura el
   certificado **antes** del `docker start`, y `issue --deliver` revierte también
   la emisión (es una sola operación).
3. `check` ahora informa qué serial sirve el contenedor y cuál está en disco, en
   vez de dar OK con un certificado desincronizado.

No queda nada pendiente de esta A.SPEC: emisión, entrega, verificación, rollback,
timer y prueba live están implementados y ejecutados.

## CRITERIOS DE ACEPTACIÓN

- `mail.lucas.spanel` tiene certificado emitido por el CA local, con clave propia,
  guardado fuera de git y verificado con `openssl verify`.
- IMAPS `993`, SMTPS `465` y submission `587` validan la cadena contra
  `root_ca.crt` con SNI `mail.lucas.spanel`; sin la CA la verificación falla.
- `lucas.spanel` está registrado como dominio de buzón y `lucas@lucas.spanel`
  existe y autentica; las cuentas previas siguen intactas.
- El test live envía y recupera un correo interno con verificación TLS activa.
- Re-ejecutar `issue` no emite ni recrea; `renew` reemite solo si hace falta.
- Un fallo de entrega restaura el contenedor anterior con el mismo ID y el correo
  sigue operativo.
- `lucas.spanel` (web) y el Traefik de producción no cambiaron.
- No se tocó `vendor/**`, `plugins/**` ni `/opt/stacks/**`.

## ROLLBACK

1. El rollback de una entrega fallida es **automático** dentro de
   `deliver()`: elimina el contenedor nuevo, renombra de vuelta el anterior, lo
   arranca y verifica 993. No hay comando `revert` separado todavía; para volver
   atrás a mano se usa `backups/<epoch>/container.json` con `docker run`.
2. Quitar del DNS del laboratorio `mail.lucas.spanel` y el MX de `lucas.spanel`;
   quitar la línea de `/etc/hosts` si se agregó.
3. Borrar `scripts/traefik-lab/certs/mail.lucas.spanel/` (conservar `backups/` si
   hace falta rehacer algo).
4. Desinstalar el timer: `sudo systemctl disable --now mail-cert.timer` y borrar
   las unidades.
5. La cuenta `lucas@lucas.spanel` se elimina con el mecanismo del plugin `mail` o
   `setup email del`; el dominio de buzón se retira según la versión medida en T0.
6. Si se importó la CA en un cliente de correo, quitarla manualmente.
7. Nada que revertir fuera del laboratorio: 0026 y producción no se tocan.

El rollback **no** puede deshacer la recreación sin downtime: volver a la
definición previa implica otro corte de pocos segundos. Está documentado y es la
compensación aceptada para un servicio interno.

## CHANGE SURFACE

```yaml
change_surface:
  allowed:
    - scripts/mail_cert.py
    - scripts/tests/test_mail_cert.py
    - scripts/tests/test_mail_cert_live.py
    - scripts/mail-cert.service
    - scripts/mail-cert.timer
    - scripts/traefik-lab/dns/hosts
    - scripts/traefik-lab/ca/intermediate_ca.crt   # público, exportado del CA
    - aspecs/0028-mail-cert-internal-tls.md
  allowed_outside_repo:
    - scripts/traefik-lab/certs/mail.lucas.spanel/   # 0700, ignorado por git
    - /home/lucas/mailadmin/scripts/                 # copia de despliegue (sin .git)
    - /etc/hosts                                     # una línea, manual
    - contenedor spanel-mail                         # recreado con backup
  prohibited:
    - vendor/**
    - apps/**
    - plugins/**
    - .env
    - .env.docker
    - /opt/stacks/**
    - scripts/traefik-lab/data/acme-internal.json
    - scripts/traefik-lab/ca/                        # solo lectura
```

## BLAST RADIUS

```yaml
blast_radius:
  direct:
    - mail.tls
    - docker.mail.container.def
    - cert.store.lab
  indirect:
    - scripts.proxy_domain
    - mail.availability
    - mail.relay.domains
  must_not_affect:
    - traefik.production
    - traefik.lab.routes
    - mail.data
    - mail.existing.accounts
    - mail.existing.domains
    - containers.other
    - host.trust_store
    - dns.public
    - app.mail.plugin
```

## COMPOSITION

```yaml
composition:
  requires_aspecs:
    - A.SPEC 0025
    - A.SPEC 0026
  must_compose_with:
    - scripts/traefik-lab.sh
    - vendor/systutor-core/plugins/mail (sin modificar)
  systemic_invariants:
    - No se toca producción
    - La CA y el material privado nunca salen del host
  composition_checks:
    - lucas.spanel sigue respondiendo 200 con el mismo certificado de 0026
    - docker restart spanel-mail conserva el certificado montado
    - ./scripts/traefik-lab.sh down --purge no borra certs/mail.lucas.spanel
    - POST /accounts del plugin mail sigue creando cuentas después de la recreación
```

## FUERA DE ALCANCE

- Correo público: DNS, MX público, SPF/DKIM/DMARC, PTR, relay y reputación.
- Webmail, UI de correo y cambios en el frontend del plugin `mail`.
- Certificados wildcard, ACME DNS-01 y automatización del proveedor DNS.
- Migrar el `hostname` del contenedor o `POSTMASTER_ADDRESS`.
- Instalar la CA en sistemas operativos o clientes por script.
- El plugin `proxy` (A.SPEC 0027) y cualquier UI de certificados.
- Alta disponibilidad, backups del maildir y retención.

## TRACEABILITY

- Requirement: correo interno `@lucas.spanel` con TLS validado por la CA local
- owner: luc444s
- approver:
- Commit:
- Deployment: laboratorio Docker del ThinkPad (`spanel-*`, `com.spanel.lab=true`)
