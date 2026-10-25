# A.SPEC 0026 — Prueba live del CLI contra el Traefik real

> `risk: high` — la prueba se ejecuta sobre el proxy de producción. El alcance
> es deliberadamente acotado: un sitio de prueba y un dominio de prueba. No
> se modifica ninguna ruta existente.

## WHY

A.SPEC 0025 entrega el laboratorio y el CLI, pero el objetivo real es que un
dominio nuevo quede funcionando con Let's Encrypt en el Traefik que ya
administra servicios en `/opt/stacks/traefik/`. Sin una prueba live no se sabe
si los entrypoints, la red `proxy`, los permisos del directorio dinámico y la
renovación se comportan como espera el CLI.

## WHAT

Ejecutar el CLI contra el servidor real y demostrar, con evidencia, que:

1. Un sitio WordPress de prueba queda accesible por HTTP y HTTPS.
2. El certificado lo emite Let's Encrypt mediante el resolver existente.
3. Traefik toma la ruta nueva sin reiniciarse.
4. Las rutas y dominios que ya existían siguen intactos.
5. Eliminar el dominio deja el sistema como estaba, salvo el sitio de prueba.

## PRE-CONDICIONES

- A.SPEC 0025 implementada y sus tests en verde.
- Acceso SSH/Docker al host del Traefik, con el mismo usuario que usa mailadmin.
- `traefik.yml` leído y entrypoints/resolvers confirmados.
- Red `proxy` existente y contenedor `traefik` en ejecución.
- Directorio `/opt/stacks/traefik/config` escribible por el mecanismo del CLI.
- Un FQDN de prueba con DNS público apuntando a la IP del servidor.
- Puerto 80 alcanzable desde Internet para HTTP-01.
- `SPANEL_PROXY_PUBLIC_IP` y `SPANEL_PROXY_ACME_EMAIL` configurados.

### Sobre `lucas.spanel1`

`lucas.spanel1` no es un FQDN público válido: `spanel1` no es un TLD y Let's
Encrypt no puede emitir para él. La prueba necesita el dominio completo, por
ejemplo `lucas.spanel1.<dominio-real>` o el nombre que el usuario confirme.
Si el objetivo es solo probar ruteo sin certificado, se hace en laboratorio
(A.SPEC 0025) y no en producción.

## CASOS DE PRUEBA

### T1 — Estado inicial y línea base

```bash
python3 scripts/proxy_domain.py doctor
docker inspect traefik --format '{{.State.StartedAt}}'
ls -la /opt/stacks/traefik/config/
```

Guardar la salida. Debe registrar el `StartedAt` y los archivos existentes
antes de tocar nada.

### T2 — Sitio de prueba

Crear o reutilizar un sitio WordPress de prueba con nombre único, por ejemplo
`lucas-test`. Verificar contenedor y volumen. Conectar el contenedor a la red
`proxy` mediante el CLI/doctor; no reiniciarlo.

### T3 — Agregar dominio en staging

```bash
python3 scripts/proxy_domain.py add \
  --site spanel-lucas-test-wp \
  --domain <fqdn-de-prueba> \
  --staging \
  --dry-run
```

Revisar el YAML impreso. Ejecutar sin `--dry-run`. El certificado puede tardar;
`status` debe pasar de `pending_cert` a `active`.

Validar:

- archivo `spanel-lucas-test.yml` creado;
- `StartedAt` de Traefik sin cambios;
- `curl -I http://<fqdn>` → `301` a HTTPS;
- `curl -I https://<fqdn>` → `200`;
- certificado emitido por el staging de Let's Encrypt.

### T4 — Repetir en producción

Eliminar el dominio de staging o pedir el certificado de producción según el
flujo del CLI. Verificar que el certificado final lo emite la CA de producción
y que `notAfter` es el esperado.

### T5 — WordPress

Si se usó `--set-wordpress-url`:

```bash
# desde el host, sin exponer contraseñas
docker run --rm --volumes-from spanel-lucas-test-wp wordpress:cli \
  wp option get home --path=/var/www/html
docker run --rm --volumes-from spanel-lucas-test-wp wordpress:cli \
  wp option get siteurl --path=/var/www/html
```

Ambos deben ser `https://<fqdn>`.

### T6 — No regresión

- Elegir al menos un dominio existente que no sea de Spanel y comprobar que
  sigue respondiendo igual (HTTP/HTTPS y certificado).
- Verificar que los YAML ajenos conservan hash y fecha.
- Confirmar que no se creó un segundo contenedor Traefik.
- Confirmar que no hubo reinicio de sitios.

### T7 — Eliminar el dominio

```bash
python3 scripts/proxy_domain.py remove --domain <fqdn-de-prueba>
```

Validar:

- el archivo del sitio se reescribió o se borró si quedó sin dominios;
- `https://<fqdn>` deja de rutear por Spanel;
- el resto de dominios sigue funcionando;
- el sitio de prueba se puede borrar después con el flujo normal de Hosting.

## EVIDENCIA

Adjuntar en el PR o en la sesión:

- salida de `doctor`;
- `StartedAt` antes y después;
- YAML generado (sin secretos);
- `curl -I` HTTP y HTTPS;
- emisor y `notAfter` del certificado;
- valores de `home`/`siteurl`;
- salida de `remove` y verificación posterior.

## CRITERIOS DE ACEPTACIÓN

- Un dominio nuevo funciona con HTTPS válido en el Traefik existente.
- No se reinició Traefik ni ningún sitio.
- No se modificó ningún archivo de configuración ajeno.
- El dominio se elimina sin dejar rutas huérfanas.
- La prueba se puede repetir de forma idempotente.

## FALLOS ESPERADOS

- DNS aún no propagado: `pending_dns`, sin pedir certificado.
- Error de ACME por rate limit: mostrar el error y no reintentar en bucle.
- Entrypoint o resolver distinto: `doctor` debe detectarlo antes de escribir.
- Permiso denegado en el directorio dinámico: abortar sin escribir.
- Certificado emitido pero WordPress con `siteurl` viejo: corregir con el flag
  de WordPress y documentar el paso manual.

## ROLLBACK

1. `proxy_domain.py remove --domain <fqdn>`.
2. Restaurar el backup del YAML si la eliminación falló.
3. Revertir `home`/`siteurl` al valor anterior con `wp option update`.
4. Borrar el sitio de prueba desde Hosting solo si se creó para esta prueba.

No se toca el contenedor `traefik` ni sus archivos existentes.

## FUERA DE ALCANCE

- UI y base de datos (A.SPEC 0027).
- DNS automático, wildcards o migración masiva de dominios.
- Cambios en Traefik, su compose o su configuración estática.
