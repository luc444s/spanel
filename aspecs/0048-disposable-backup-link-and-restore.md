# A.SPEC 0048 — Importar/exportar: enlace desechable y restauración del mismo ZIP

> `risk: high` — la exportación es de solo lectura, pero **Importar restaura y
> reemplaza archivos y, en WordPress, la base de datos**. Esta etapa sigue el
> patrón prototype-first: scripts operativos probados antes de tocar el panel.

## WHY

A.SPEC 0047 ya produce un único `.zip` portable y verificable para WordPress
(archivos + una sola base) y para sitios estáticos (archivos). Falta cerrar el
round-trip mínimo:

1. exportar ese ZIP mediante un enlace temporal de un solo uso;
2. importar **exactamente el mismo formato** y restaurarlo al mismo contenedor.

No se agregan dos formatos ni caminos alternativos. El ZIP que se descarga es
el ZIP que se restaura.

## SCOPE — etapa actual

### Exportar: `scripts/site_backup_link.py`

- Entrada: `--zip <backup.zip>` ya creado por `scripts/site_backup.py`.
- Antes de escuchar, ejecuta la verificación de A.SPEC 0047 (manifest, hashes,
  tamaños, `site/**` y SQL obligatorio para WordPress).
- Genera un token opaco con 256 bits de entropía y sirve una sola ruta exacta.
- TTL configurable con `--ttl` (default 300 s, máximo 3600 s).
- El primer `GET` válido consume el token **antes** de transmitir. Dos GET
  concurrentes nunca reciben ambos el archivo.
- `HEAD` puede consultar headers sin consumir el token.
- La respuesta es streaming con `Content-Length`, `Content-Disposition`,
  `Cache-Control: no-store`, `Referrer-Policy: no-referrer` y
  `X-Content-Type-Options: nosniff`.
- El proceso termina tras completar/interrumpir el primer GET o al expirar.
- Seguridad por defecto: bind en `127.0.0.1`; escuchar en otra interfaz exige
  declarar explícitamente `--public-base-url`. HTTP no-local se rechaza salvo
  opt-in `--allow-insecure-http`; para uso real se pasan `--tls-cert/--tls-key`
  y un origen HTTPS.

Ejemplo:

```bash
python3 scripts/site_backup_link.py \
  --zip /root/backups/wp-tutorabusiness-20261107-040000.zip \
  --bind 0.0.0.0 --port 8765 \
  --public-base-url https://backups.example.com:8765 \
  --tls-cert /path/fullchain.pem --tls-key /path/privkey.pem --ttl 300
```

### Importar: `scripts/site_restore.py`

- Entrada: `--zip <backup.zip> --container <container> --yes`.
- Reutiliza y endurece el contrato de verificación de A.SPEC 0047:
  - rechaza entries extra, duplicadas, absolutas, con `..`, backslash,
    symlinks, manifest incompleto o hashes/tamaños distintos;
  - extrae por streaming a un temporal privado, nunca con `extractall`;
  - impone un máximo de bytes descomprimidos configurable.
- Inspecciona el contenedor actual y exige:
  - mismo `manifest.container`;
  - mismo tipo `wp|static`;
  - contenedor corriendo;
  - path de destino resuelto por el mismo contrato de A.SPEC 0047.
- Nunca usa `db_host` del manifest para ejecutar comandos. En WordPress la DB,
  usuario y clave se resuelven otra vez desde el contenedor actual o mediante
  overrides explícitos.
- Sin `--yes` solo muestra el plan y no muta.
- Con `--yes`, antes de la primera mutación crea un **respaldo de seguridad**
  nuevo del estado actual usando `site_backup.py`. Si ese respaldo falla, la
  restauración no empieza.
- Archivos: prepara staging, reemplaza el contenido del web root y limpia el
  staging. No toca otros paths del contenedor.
- WordPress: activa mantenimiento, elimina las tablas de **esa única DB**,
  importa `database/backup.sql`, reemplaza archivos y retira mantenimiento.
- Si falla después de comenzar a mutar, intenta rollback automático con el ZIP
  de seguridad. Si también falla el rollback, termina con un error explícito y
  conserva ambos ZIPs/temporales necesarios para recuperación manual.
- No imprime passwords, SQL ni contenido de `wp-config.php`. El token aparece
  únicamente en la línea intencional `LINK:`; se deshabilita el access log HTTP
  para que no vuelva a quedar registrado.

Ejemplos:

```bash
# plan: verifica ZIP + destino, cero mutaciones
python3 scripts/site_restore.py --zip backup.zip --container tutorabusiness

# restauración confirmada
python3 scripts/site_restore.py --zip backup.zip --container tutorabusiness --yes
```

## Fuera de alcance

- Panel, modal, endpoints HTTP del plugin y permisos RBAC.
- Descarga directa permanente o autenticada: en esta etapa solo hay link
  temporal de un uso.
- Historial, catálogo, retención, purge, S3 u object storage.
- Restaurar a otro nombre de contenedor, convertir WP ↔ estático o clonar.
- Programación periódica y jobs/workers.
- Cambiar el formato ZIP de A.SPEC 0047.

La integración Importar/Exportar dentro del modal será la etapa siguiente,
después de verificar este restore en un laboratorio real.

## CONTRACT

Precondiciones:

- Python 3.12 stdlib, `docker` CLI accesible y contenedor destino corriendo.
- ZIP válido generado por `scripts/site_backup.py`.
- Para WordPress, DB accesible con las credenciales actuales del contenedor.

Postcondiciones:

1. El enlace entrega exactamente los bytes del ZIP validado una sola vez.
2. Un ZIP expirado/consumido no vuelve a ser descargable por ese proceso.
3. Restaurar un estático deja su web root con los archivos de `site/**`.
4. Restaurar WordPress deja web root + su única DB con el contenido del ZIP.
5. Antes de mutar existe un ZIP verificable del estado anterior.
6. Un fallo intenta rollback y nunca afecta otra DB ni otro contenedor.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: Un enlace válido produce como máximo una respuesta 200 con el ZIP.
    proof: test de dos GET concurrentes; exactamente uno recibe 200/contenido.
  - id: I2
    statement: Solo un ZIP íntegro de A.SPEC 0047 puede compartirse o restaurarse.
    proof: tests de hash, tamaño, entry extra, traversal, symlink y SQL faltante.
  - id: I3
    statement: Restore nunca muta antes de crear y verificar el safety backup.
    proof: fake Docker; fallo de backup deja cero llamadas mutantes.
  - id: I4
    statement: Un ZIP solo restaura al mismo container y al mismo kind.
    proof: mismatch de container/kind falla antes de staging/DB.
  - id: I5
    statement: WordPress toca una sola DB, resuelta del target actual, nunca del manifest.
    proof: comandos registrados no contienen --all-databases ni otra DB fixture.
  - id: I6
    statement: Tras una falla mutante se intenta rollback con el safety ZIP.
    proof: inyectar fallo en import SQL/copia y verificar segundo apply del safety ZIP.
  - id: I7
    statement: Credenciales no aparecen en output; el token solo aparece una vez en LINK y nunca en access logs/errores.
    proof: capturar stdout/stderr con secretos fixture y handlers sin log_message.
```

## VERIFICATION

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin
.venv/bin/python -m pytest \
  scripts/tests/test_site_backup.py \
  scripts/tests/test_site_backup_link.py \
  scripts/tests/test_site_restore.py -q

.venv/bin/ruff check \
  scripts/site_backup.py \
  scripts/site_backup_link.py \
  scripts/site_restore.py \
  scripts/tests/test_site_backup_link.py \
  scripts/tests/test_site_restore.py
```

Laboratorio:

1. Crear ZIP estático, descargarlo por el link y comprobar SHA256 byte a byte.
2. Modificar el estático, restaurar el ZIP y verificar contenido HTTP anterior.
3. Crear WP disposable, respaldar, mutar archivo + fila DB, restaurar y verificar
   ambos valores anteriores.
4. Forzar error de import SQL y comprobar rollback al estado pre-restore.

## Evidencia E2E de laboratorio — 2026-11-07

Ejecutado en `lucas-ThinkPad-E570` (`100.67.5.50`), Docker 29.1.3, con
contenedores y red `spanel-lab-0048-*` desechables:

- **Link one-time real**: backup estático servido por HTTP loopback; la descarga
  coincidió byte a byte con el ZIP, el proceso informó `enlace consumido`, un
  segundo GET no pudo reutilizarlo y otro link sin GET terminó con exit 2 por
  expiración.
- **Restore estático real** (`nginx:alpine`): después del backup se cambió
  `index.html` y se creó `extra.txt`; el plan sin `--yes` no mutó; el restore
  recuperó el contenido original, eliminó el archivo posterior y dejó safety
  ZIP verificable.
- **Restore WordPress real** (`wordpress:php8.3-apache` + `mariadb:11.7`): ZIP
  de 31.7 MB comprimidos, 3.950 entries/81.63 MB originales (3.949 archivos +
  dump de una DB). Después de mutar archivo, fila SQL y crear una tabla extra,
  el restore recuperó archivo y fila originales y eliminó la tabla posterior.
- **Rollback real**: se usó un ZIP íntegro según manifest pero con SQL
  semánticamente inválido al final. La importación falló después de vaciar/mutar
  la DB, devolvió exit 1, ejecutó `ROLLBACK: estado previo restaurado`, recuperó
  archivo + fila DB del safety backup y retiró `.maintenance`.
- Cleanup verificado: cero containers y redes `spanel-lab-0048-*` restantes.

Evidencia local fuera del repo:
`/data/data/com.termux/files/usr/tmp/opencode/aspec0048-e2e-evidence/work/`.
El HTTPS público no formó parte de este E2E; el transporte probado fue loopback
y las validaciones TLS/configuración permanecen cubiertas offline.

## ROLLBACK

- Eliminar los dos scripts nuevos; no hay migraciones ni cambios al panel.
- Cada restore confirmado deja su safety backup para recuperación manual.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0048-disposable-backup-link-and-restore.md
    - scripts/site_backup_link.py
    - scripts/site_restore.py
    - scripts/tests/test_site_backup_link.py
    - scripts/tests/test_site_restore.py
  prohibited:
    - vendor/**
    - apps/**
    - docker-compose.yml
    - .env*
```

## Traceability

- Requirement: unir Importar/Exportar usando un único ZIP; en esta etapa
  Exportar solo genera un link temporal y Importar solo restaura ese ZIP.
- owner: luc444s

## Definition of Done

- [x] Link sirve exactamente una descarga y expira.
- [x] Restore estático round-trip probado offline + lab.
- [x] Restore WP archivos + una DB probado offline + lab.
- [x] Safety backup bloquea mutación si falla y permite rollback si apply falla.
- [x] Tests y ruff verdes; ningún secreto en output.
