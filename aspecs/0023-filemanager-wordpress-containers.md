# A.SPEC 0023 — Gestor de archivos reutilizable para sitios WordPress

> `risk: high` — leer, editar y borrar archivos del contenedor puede exponer
> credenciales de WordPress o cambiar código en ejecución. Este documento
> define el contrato de la primera versión del plugin.

## WHY

Hosting permite administrar sitios WordPress, pero para mover o modificar sus
archivos hoy hace falta entrar al host Docker. Se necesita una interfaz tipo
cPanel dentro de SPanel. La interfaz y las operaciones de archivos deben poder
reutilizarse más adelante con otro tipo de destino, sin fijar en el gestor ni
un nombre de contenedor ni `/var/www/html`.

## WHAT

Crear el plugin independiente `filemanager`. En el detalle de cada sitio
WordPress asignado a un tenant aparece un botón **Gestor de archivos** que
abre su explorador. La primera versión incluye:

- navegar carpetas, ver nombre, tipo, tamaño y fecha de modificación;
- subir uno o varios archivos mediante **Subir archivos** o arrastrar y soltar,
  con progreso y resultado individual;
- descargar archivos; comprimir una selección de archivos/carpetas a ZIP,
  descargar el ZIP y descomprimir ZIP en una carpeta elegida;
- crear carpetas, renombrar archivos/carpetas y borrar con confirmación;
- abrir y guardar archivos de texto pequeños en un editor mínimo. Los binarios
  se descargan o se sustituyen mediante subida, no se abren en el editor.

No se posponen estas operaciones para otra versión. ZIP es el formato de
archivo comprimido de esta primera entrega.

## ARQUITECTURA Y ALCANCE

1. `plugins/filemanager/` es un plugin con manifiesto, backend, frontend,
   permisos, documentación y pruebas propios. Es dueño de las operaciones,
   UI y transporte binario; no importa modelos ni servicios de Hosting.
2. Un contrato pequeño de **proveedor de destinos** conecta plugins. Recibe
   `provider` + identificador de recurso y contexto del usuario/tenant; devuelve
   solamente un destino autorizado: identidad estable del recurso, contenedor,
   raíz permitida y capacidades de lectura/escritura. Hosting registra el
   proveedor `hosting` para un `hosting_sites.id` existente y propiedad del
   tenant. La integración se conecta en la composición de plugins, no mediante
   un parámetro `container` o `root` recibido del cliente. El SDK actual no
   incluye este registro: añadir únicamente el contrato/registro necesario,
   manteniendo compatibilidad con plugins existentes.
3. Hosting configura internamente la raíz de sus WordPress; inicialmente
   `/var/www/html`. El valor no aparece hardcodeado en `filemanager` ni lo
   selecciona el navegador. El contrato admite raíces distintas por recurso
   en el futuro. No hace falta un formulario para cambiarla en esta versión.
4. El frontend de Hosting agrega el botón **Gestor de archivos** en el detalle
   de un sitio en marcha cuando `filemanager` está habilitado y el usuario
   tiene los permisos pertinentes. Enlaza a la ruta frontend del nuevo plugin
   para el destino `hosting/{site_id}`. El explorador recibe rutas **relativas
   a la raíz autorizada**; sus migas de pan nunca navegan por encima de ella.
5. Primer proveedor: contenedores WordPress de sitios asignados. El gestor no
   enumera todos los contenedores Docker, no expone el filesystem del host y no
   requiere modificar WordPress, MariaDB, sus permisos ni sus volúmenes.

## CONTRATO DE API

Base propuesta: `/api/v1/plugins/filemanager/targets/{provider}/{resource_id}`.
`path` y `destination` son rutas relativas; el servidor calcula la ruta
efectiva tras resolver el destino. El esquema final puede separar operaciones
en endpoints específicos, pero debe conservar estos comportamientos:

| Operación | Entrada | Resultado |
|---|---|---|
| Listar | `GET /entries?path=...` | Directorios y archivos, sin contenidos |
| Leer texto | `GET /text?path=...` | Contenido acotado + versión de archivo |
| Guardar texto | `PUT /text?path=...` + versión | Guardado atómico; conflicto si cambió |
| Subir | `POST /uploads?path=...` + archivos | Estado por archivo; mismo destino para botón y drag & drop |
| Descargar | `POST /download-tickets?path=...` + `GET /download-tickets/{ticket}` | Ticket de un solo uso (30 s) y descarga nativa en streaming |
| Crear carpeta | `POST /directories` + padre/nombre | Nueva carpeta |
| Renombrar | `PATCH /entries` + ruta/nuevo nombre | Mismo directorio, sin sobrescritura |
| Borrar | `DELETE /entries?path=...` | Borrado confirmado en UI; carpeta puede ser recursiva |
| Comprimir | `POST /archives` + selección/nombre ZIP | ZIP guardado en carpeta autorizada |
| Descomprimir | `POST /archives/extract` + ZIP/destino | Entradas extraídas y estado |

- Todo endpoint vuelve a resolver destino, usuario activo, tenant y permisos.
  Recurso inexistente o de otro tenant responde igual (`404`). No basta con
  ocultar botones: el servidor bloquea el acceso sin autorización.
- Permisos propios `filemanager.files.read` (listar, leer y descargar, incluidos
  archivos potencialmente sensibles) y `filemanager.files.manage` (subir,
  editar, crear, renombrar, borrar, comprimir y extraer), además de comprobar
  la propiedad y el permiso del recurso en el proveedor. La asignación de
  estos permisos es explícita; `hosting.sites.read` por sí solo no da acceso a
  `wp-config.php` ni a otros archivos.
- Definir límites configurables de tamaño por archivo, tamaño de ZIP, número
  de entradas y bytes totales descomprimidos. Rechazar exceso de cuota antes
  de dejar cambios parciales. La UI muestra el límite y el motivo de rechazo.
- Descargar en streaming sin cargar archivos grandes en memoria ni exponer
  rutas/credenciales de Docker en la URL pública. El ticket opaco no contiene
  la ruta ni reemplaza la autorización: al canjearlo se revalidan usuario,
  tenant, permiso y sitio. Evitar conexiones SQL
  ocupadas durante subidas, descargas, ZIP o esperas a Docker: autorizar con
  sesión corta, cerrarla y después iniciar el trabajo binario.
- El transporte actual `transport.run` captura texto: las transferencias
  requieren una vía binaria nueva que funcione tanto en Docker local como por
  SSH, sin TTY, con cierre de procesos/pipes ante error o desconexión y
  backpressure. No construir comandos de shell a partir de rutas del usuario.
  Las escrituras respetan el usuario/permisos efectivos del contenedor; no
  aplicar `chmod -R` ni cambiar propietario del sitio para que funcione.

## COMPORTAMIENTO DE LA INTERFAZ

- **Gestor de archivos** abre la ruta del plugin con el sitio seleccionado.
  Listado ordenado, navegación por doble clic o acción Abrir, migas de pan,
  selección múltiple para comprimir, y acciones visibles según permisos.
- **Subir archivos** acepta selección múltiple. Arrastrar archivos al área de
  la carpeta actual abre la misma cola: progreso y errores por archivo, sin
  perder el resultado de los demás. Un nombre existente no se sobrescribe en
  silencio: ofrecer reemplazar, conservar ambos u omitir; omitir es el valor
  inicial. Sustituir solo tras completar una copia temporal válida.
- **Editor mínimo**: texto UTF-8 dentro de un límite pequeño configurable,
  Guardar/Cancelar y aviso de cambios sin guardar. No sobreescribir cambios
  hechos desde otra sesión: responder `409` ante versión obsoleta y ofrecer
  recargar. Binarios y archivos que excedan el límite no se abren como texto.
- **Borrar** pide confirmación con nombre y advierte si se borra una carpeta
  completa; nunca permite borrar la raíz. Renombrar y crear carpeta muestran
  error si el nombre ya existe. Después de cada operación se actualiza el
  listado; una acción fallida no presenta éxito.
- **ZIP**: comprimir guarda el archivo en la carpeta actual; descomprimir
  muestra destino y resumen antes de ejecutar. Si alguna entrada existe,
  usar política explícita de omitir/reemplazar, nunca sobrescribir por defecto.

## INVARIANTES

```yaml
invariants:
  - id: I1
    statement: Ningún cliente puede seleccionar arbitrariamente contenedor o raíz.
    proof: El proveedor resuelve ambos desde un recurso propio en cada petición.
  - id: I2
    statement: Ni rutas ni ZIP ni enlaces simbólicos pueden escapar de la raíz autorizada.
    proof: Validar componentes y resolución efectiva dentro del contenedor; rechazar rutas absolutas, .., enlaces hacia fuera, ZIP Slip y enlaces de archivo comprimido. Revalidar antes de escribir para evitar cambios de ruta entre comprobación y uso.
  - id: I3
    statement: No se confunden los archivos de dos tenants o dos sitios.
    proof: Resolver ownership y permiso por operación; probar IDs ajenos, cambios de propietario, recurso eliminado y contenedores con nombres parecidos.
  - id: I4
    statement: Una subida, descarga o compresión lenta no retiene conexiones SQL.
    proof: Medir conexiones ocupadas con 30+ transferencias simultáneas y upstream lento, incluidos errores y desconexiones.
  - id: I5
    statement: Ningún fallo deja un reemplazo parcial ni un ZIP descomprimido fuera de la raíz.
    proof: Escritura temporal + renombrado atómico para archivos individuales; validar todas las entradas y cuotas antes de extraer; limpiar temporales ante fallo.
  - id: I6
    statement: El contenido y las credenciales no se escriben en logs ni auditoría.
    proof: Auditar actor, sitio, operación, rutas relativas y resultado; nunca bytes, contenido del editor, secretos ni comandos con credenciales.
```

## VERIFICACIÓN

- Pruebas de contratos del proveedor: Hosting solo entrega sitios propios en
  marcha, otro proveedor de prueba puede entregar otra raíz sin cambios en el
  plugin, y la desactivación del plugin retira su ruta y botón.
- Pruebas de autorización para lectura/escritura y revocación, otro tenant,
  sitio eliminado, rutas `..`, rutas absolutas, enlaces simbólicos y cambio
  concurrente de enlaces durante una operación.
- Pruebas de subida múltiple binaria, descarga íntegra, duplicados,
  renombrado, borrado recursivo, creación de carpetas, conflicto del editor,
  ZIP normal y ZIP malicioso (`../`, rutas absolutas, enlaces, bombas por
  tamaño/cantidad). Verificar limpieza tras error y desconexión.
- Prueba concurrente con 30+ transferencias y pool SQL reducido, más build del
  frontend, Ruff, Pyright y tests del backend.
- En el Docker del laptop, probar sobre un sitio WordPress de prueba asignado
  al tenant: subir un archivo temporal, editarlo, renombrarlo, comprimirlo,
  descargarlo, extraerlo y borrarlo. Confirmar que otro tenant no ve el
  destino. No tocar contenido productivo ni otros contenedores.

## FUERA DE ALCANCE

- Un gestor de contenedores arbitrarios, acceso al filesystem del host o un
  terminal/shell en el navegador.
- Editar binarios, gestión de propietarios/permisos Unix, búsqueda global,
  sincronización masiva, subida de carpetas completas y formatos de archivo
  distintos de ZIP.
- Un formulario de administración de raíz por sitio: la raíz es configurable
  internamente por el proveedor y puede exponerse en una versión futura.

## ROLLBACK

Deshabilitar `filemanager` oculta su vista, botón y rutas; los archivos ya
modificados por usuarios no se revierten automáticamente. Revertir el plugin
y su integración de Hosting restaura el comportamiento anterior sin cambiar
las bases de datos ni los permisos de MariaDB.
