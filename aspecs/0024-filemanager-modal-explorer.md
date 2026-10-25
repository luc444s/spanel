# A.SPEC 0024 — Explorador de archivos tipo escritorio dentro de un modal

> `risk: medium` — cambia la presentación del gestor de archivos y agrega
> interacción directa (clic, doble clic, menú contextual). No cambia el
> contrato de backend de A.SPEC 0023 ni los permisos.

## WHY

El gestor actual funciona, pero se siente como un formulario: la tabla es
plana, no hay accesos rápidos, ni barra de estado, ni metáfora de ventana. Los
usuarios vienen de cPanel/exploradores de escritorio y esperan navegar con
doble clic, menú contextual y una ventana reconocible. Además, entrar al
gestor no debería sacarlos del contexto de Hosting: al cerrar deben volver al
sitio que estaban viendo.

## WHAT

Convertir la pantalla del gestor en un **modal con apariencia de explorador
de PC**, manteniendo intactos los endpoints, permisos y reglas de seguridad
de A.SPEC 0023.

- El modal se abre desde el botón **Gestor de archivos** del detalle de un
  sitio en Hosting.
- La ruta `filemanager/:provider/:resourceId` se conserva como enlace directo.
  El contenido se dibuja como una ventana modal centrada sobre un fondo
  atenuado; cerrar (X, Escape o botón Volver) navega a `/app/hosting`.
- En escritorio: ventana de hasta `1600px`/`96vw` y `90vh`, con barra de título,
  barra de navegación, barra de acciones, panel lateral, tabla y barra de
  estado. En celular: hoja a pantalla completa, panel lateral reemplazado por
  un selector de accesos y acciones secundarias agrupadas.
- Sin cambios de backend ni de permisos: `filemanager.files.read` y
  `filemanager.files.manage` siguen mandando en cada operación.

## INTERFAZ

### Estructura

1. **Barra de título**: icono, “Gestor de archivos”, nombre del sitio y botón
   de cierre accesible (`aria-label="Cerrar"`). La barra de título, navegación
   y acciones usan el token de chrome del tema (`sidebar` / `sidebar-muted`)
   para verse oscuras y consistentes con el resto de la aplicación.
2. **Barra de navegación**: atrás, adelante, subir, migas de pan clickeables,
   botón de refrescar, campo de búsqueda local y, en celular, selector de
   accesos.
3. **Barra de acciones**: Subir archivos, Nueva carpeta, Descargar, Comprimir
   ZIP, Descomprimir, Renombrar y Borrar. Cada acción se habilita según
   permisos, selección y tipo de elemento.
4. **Panel lateral** (escritorio): “Inicio” y carpetas de primer nivel
   obtenidas del listado de la raíz, más el uso de espacio si el backend lo
   expone (no se inventa). En celular se muestra como menú desplegable.
5. **Tabla**: columnas Nombre, Tipo, Tamaño, Modificado y acciones por fila.
   Encabezados ordenables (nombre, tipo, tamaño, fecha) con carpetas primero.
6. **Barra de estado**: cantidad de elementos, cantidad seleccionada y progreso
   de subida activo.
7. **Modos de vista**: selector Lista / Mosaico en la barra de navegación. La
   lista usa la tabla de columnas; el mosaico usa tarjetas con icono grande por
   tipo de archivo, nombre y detalle. La elección se recuerda en el navegador
   (`filemanager:view`) y ambos modos comparten selección, orden, búsqueda,
   doble clic, menú contextual y pulsación larga.

### Interacción

- **Escritorio (puntero fino)**:
  - clic izquierdo selecciona;
  - `Shift` + clic selecciona un rango;
  - `Ctrl`/`Cmd` + clic agrega o quita;
  - doble clic izquierdo abre carpeta o archivo;
  - arrastrar con clic izquierdo desde el fondo (fuera de filas y tarjetas)
    dibuja un rectángulo de selección; reemplaza la selección y con
    `Ctrl`/`Cmd`/`Shift` la suma; un clic simple en el fondo la limpia;
  - clic derecho abre menú contextual: Abrir, Descargar, Renombrar,
    Comprimir, Descomprimir (solo ZIP) y Borrar. En el fondo, el menú ofrece
    Subir archivos, Nueva carpeta, Actualizar e Ir al inicio.
- **Táctil (puntero grueso)**:
  - un toque abre carpeta o archivo;
  - mantener presionado abre el menú contextual;
  - los checkboxes permiten selección múltiple sin abrir.
- **Teclado**: `Escape` cierra el modal o el diálogo interno activo; `Enter`
  abre; `F2` renombra; `Supr` borra; `Backspace` sube una carpeta.
- **Arrastrar y soltar** sobre la tabla o el modal sube archivos a la carpeta
  actual, con overlay visible y la misma cola de progreso por archivo.
- **Búsqueda** filtra por nombre dentro de la carpeta actual; no navega ni
  consulta el backend.
- **Orden** es local a la vista; el orden natural del backend se conserva al
  entrar a una carpeta.

### Diálogos

- Renombrar, nueva carpeta, comprimir y conflicto de subida usan los diálogos
  propios del plugin, apilados **encima** del modal con mayor `z-index`.
- El editor de texto ocupa una ventana amplia (`max-w-5xl`, área de edición de
  `60vh` con mínimo de `320px` y redimensionable) para poder modificar el
  archivo cómodamente, con Guardar/Cancelar fijos al pie.
- Borrar y descomprimir piden confirmación con nombre y consecuencias.
- Descartar cambios del editor pide confirmación solo si el contenido cambió.
- Mientras hay un diálogo interno activo, `Escape` cierra ese diálogo y no el
  modal completo; un clic en el fondo del modal no descarta cambios.

### Estados

- Carga inicial: placeholder sin saltos de layout.
- Carpeta vacía: mensaje explícito dentro de la tabla.
- Error de listado: alerta con reintento (refrescar).
- Sin permiso de escritura: acciones de mutación ocultas y leyenda de modo
  lectura.
- Subida con conflicto: reemplazar, renombrar u omitir; omitir por defecto.

## ARQUITECTURA

- `frontend/FileManagerModal.tsx`: cascarón visual (overlay, ventana, barra de
  título, slots, Escape, foco inicial). No conoce la API.
- `frontend/FileExplorer.tsx`: estado y operaciones (listado, navegación,
  selección, orden, búsqueda, subida, acciones). Reutiliza los endpoints de
  A.SPEC 0023.
- `frontend/FileTable.tsx`: tabla, iconos, selección, doble clic, menú
  contextual y pulsación larga.
- `frontend/FileSidebar.tsx`: accesos rápidos a partir de la raíz.
- `frontend/FileDialogs.tsx` y `frontend/format.ts`: diálogos y formato ya
  existentes, ampliados si hace falta.
- `frontend/FileManagerPage.tsx`: lee parámetros, permisos y navegación de
  cierre; renderiza el explorador en el modal.
- Sin imports de Hosting dentro de `filemanager`: la integración sigue siendo
  el botón + ruta. No se agregan contratos nuevos al SDK.

## INVARIANTES

```yaml
invariants:
  - id: I1
    statement: El modal no relaja ninguna regla de autorización.
    proof: Sigue usando los endpoints y permisos de A.SPEC 0023; el backend revalida cada operación.
  - id: I2
    statement: Cerrar el modal no pierde cambios sin avisar.
    proof: El editor y los diálogos internos piden confirmación; Escape respeta el diálogo activo.
  - id: I3
    statement: La navegación sigue confinada a la raíz autorizada.
    proof: La UI solo compone rutas relativas; el backend valida cada ruta.
  - id: I4
    statement: El comportamiento cambia según el tipo de puntero, no según el tamaño de pantalla.
    proof: Detectar pointer coarse/fine con matchMedia y cubrir ambos caminos.
  - id: I5
    statement: La vista no inventa datos que el backend no entrega.
    proof: Tipo, tamaño y fecha salen del listado; el uso de espacio solo se muestra si existe una fuente real.
```

## VERIFICACIÓN

- `tsc --noEmit` y build de `apps/web` sin errores.
- Tests de backend de `filemanager` y `hosting` siguen pasando.
- Test unitario de formateo/orden si se agregan helpers puros.
- Prueba manual en 5173 sobre `spanel-demowp-wp`: abrir modal, navegar,
  ordenar, buscar, seleccionar con Ctrl/Shift, doble clic, menú contextual,
  cambiar entre lista y mosaico, subir con conflicto, renombrar, borrar,
  comprimir y descomprimir.
- Prueba táctil: un toque abre, pulsación larga muestra el menú, acciones
  visibles sin desbordar en pantalla angosta.

## FUERA DE ALCANCE

- Cambios de backend, nuevos endpoints o permisos.
- Copiar/mover entre carpetas, papelera, búsqueda recursiva o subida de
  carpetas completas.
- Miniaturas, previsualización de imágenes o edición binaria.
- Mantener Hosting montado detrás del modal: la ruta conserva el enlace
  directo y al cerrar se vuelve al listado de Hosting.

## ROLLBACK

Revertir el commit de frontend y desplegar restaura la página anterior del
gestor. No hay migraciones ni cambios de datos: los archivos y permisos
permanecen iguales.
