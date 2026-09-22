# Core UI Components

Componentes reutilizables del core frontend. Viven en `apps/web/src/shared/ui/` y son importables desde cualquier plugin o página.

Estilo: utility-first con Tailwind CSS, colores semánticos vía variables CSS, sin colores hardcodeados.

---

## Form patterns (obligatorio — leer antes de crear cualquier formulario)

### Label + input

```tsx
<label className="block space-y-2 text-sm text-foreground">
  <span>Nombre del campo</span>
  <Input value={val} onChange={fn} />
  {/* o <Combobox />, <Select />, <Textarea /> */}
</label>
```

Nunca: `text-xs`, `text-muted-foreground` en labels, asteriscos rojos para required.

### Form layout

```tsx
<form className="space-y-6" onSubmit={handleSubmit}>
  {error && <Alert title="Error">{error}</Alert>}

  <div className="grid gap-4 md:grid-cols-2">
    <label className="block space-y-2 text-sm text-foreground">...</label>
    <label className="block space-y-2 text-sm text-foreground">...</label>
  </div>

  <div className="flex justify-end gap-3">
    <Button type="button" variant="secondary">Cancelar</Button>
    <Button type="submit">Guardar</Button>
  </div>
</form>
```

### Que componente usar

| Necesidad | Componente | Import |
|-----------|-----------|--------|
| Input texto/fecha | `Input` | `shared/ui/input` |
| Multilinea | `Textarea` | `shared/ui/input` |
| Select con busqueda | `Combobox` | `shared/ui/combobox` |
| Select simple | `Select` | `shared/ui/select` |
| Boton | `Button` | `shared/ui/button` |
| Error | `Alert` | `shared/ui/alert` |
| Modal | `Dialog` | `shared/ui/dialog` |
| Checkbox | `Checkbox` | `shared/ui/input` |

### Prohibido

- `<button>` con estilos inline → usar `<Button>`
- `<textarea>` nativo → usar `<Textarea>`
- Divs rojos para errores → usar `<Alert>`
- Asteriscos rojos `*` en labels
- `text-xs` o `text-muted-foreground` en labels
- `style={{}}` o colores hardcodeados

---

## alert.tsx — `Alert`

```tsx
import { Alert } from "ruta/al/shared/ui/alert";
```

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `title` | `string` | requerido | Título del mensaje |
| `children` | `ReactNode` | — | Contenido descriptivo |

---

## badge.tsx — `Badge`

```tsx
import { Badge } from "ruta/al/shared/ui/badge";
```

Badge genérico. Acepta `className` para personalizar color de estado.

```tsx
<Badge className="bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300">Activo</Badge>
```

---

## button.tsx — `Button`

```tsx
import { Button } from "ruta/al/shared/ui/button";
```

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `variant` | `"primary" \| "secondary"` | `"primary"` | Estilo visual |
| `...props` | `ButtonHTMLAttributes` | — | Pasa al elemento `<button>` |

```tsx
<Button>Guardar</Button>
<Button variant="secondary">Cancelar</Button>
<Button disabled>Enviando...</Button>
```

---

## card.tsx — `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardContent`

```tsx
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "ruta/al/shared/ui/card";
```

```tsx
<Card>
  <CardHeader>
    <CardTitle>Título</CardTitle>
    <CardDescription>Subtítulo o descripción</CardDescription>
  </CardHeader>
  <CardContent>Contenido del card</CardContent>
</Card>
```

---

## combobox.tsx — `Combobox`

```tsx
import { Combobox, type ComboboxOption } from "ruta/al/shared/ui/combobox";
```

Select con buscador inline. Recomendado cuando hay más de 5-8 opciones.

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `value` | `string` | requerido | Valor seleccionado |
| `onChange` | `(value: string) => void` | requerido | Callback al seleccionar |
| `options` | `ComboboxOption[]` | requerido | `{ value, label, keywords? }` |
| `placeholder` | `string` | — | Texto cuando no hay selección |
| `searchPlaceholder` | `string` | `"Buscar..."` | Placeholder del input de búsqueda |
| `emptyMessage` | `string` | `"Sin opciones."` | Mensaje cuando no hay resultados |
| `disabled` | `boolean` | `false` | Deshabilitado |

```tsx
<Combobox
  value={form.type}
  onChange={(val) => setForm({ ...form, type: val })}
  options={[
    { value: "GLOBAL", label: "Global", keywords: ["global"] },
    { value: "PRODUCT", label: "Producto", keywords: ["producto"] },
  ]}
  placeholder="Seleccionar tipo"
/>
```

---

## data-table.tsx — `DataTable`

```tsx
import { DataTable, type DataTableColumn } from "ruta/al/shared/ui/data-table";
```

Tabla genérica con soporte de filas clickeables y modo denso.

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `columns` | `DataTableColumn<Row>[]` | requerido | `{ key, header, render, className? }` |
| `rows` | `Row[]` | requerido | Datos de la tabla |
| `rowKey` | `(row: Row) => string` | requerido | Función para obtener key única |
| `emptyMessage` | `string` | requerido | Mensaje cuando no hay filas |
| `onRowClick` | `(row: Row) => void` | — | Callback al hacer click en fila |
| `dense` | `boolean` | `false` | Reduce padding de celdas |

```tsx
<DataTable
  columns={[
    { key: "name", header: "Nombre", render: (row) => row.name },
    { key: "email", header: "Email", render: (row) => row.email ?? "-" },
  ]}
  rows={data}
  rowKey={(row) => row.id}
  emptyMessage="No hay registros."
  onRowClick={(row) => handleEdit(row)}
/>
```

---

## dialog.tsx — `Dialog`

```tsx
import { Dialog } from "ruta/al/shared/ui/dialog";
```

Modal básico. Se cierra al hacer click fuera o en "Cerrar".

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `open` | `boolean` | requerido | Control de visibilidad |
| `title` | `string` | requerido | Título del modal |
| `description` | `string` | — | Subtítulo opcional |
| `children` | `ReactNode` | requerido | Contenido del modal |
| `actions` | `ReactNode` | — | Footer de acciones |
| `onClose` | `() => void` | requerido | Callback al cerrar |
| `maxWidthClassName` | `string` | `"max-w-2xl"` | Clase Tailwind para ancho máximo |

```tsx
<Dialog open={isOpen} title="Nuevo cliente" onClose={() => setIsOpen(false)}>
  <form>...</form>
</Dialog>
```

---

## input.tsx — `Input`

```tsx
import { Input } from "ruta/al/shared/ui/input";
```

Wrapper de `<input>` con estilos del sistema. Pasa todas las props estándar de HTMLInputElement.

```tsx
<Input value={name} onChange={handleChange} placeholder="Nombre del cliente" />
<Input type="date" value={date} onChange={handleDateChange} />
```

---

## location-picker.tsx — `LocationPicker`

```tsx
import { LocationPicker } from "ruta/al/shared/ui/location-picker";
```

Selector de ubicación geográfica con mapa interactivo (Leaflet + OpenStreetMap). Incluye búsqueda por dirección vía Nominatim.

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `value` | `{ lat: number; lng: number } \| null` | requerido | Ubicación actual |
| `onChange` | `(location: { lat: number; lng: number }) => void` | requerido | Callback al cambiar ubicación |
| `className` | `string` | — | Clase adicional |
| `placeholder` | `string` | `"Haz clic en el mapa para seleccionar una ubicación"` | Texto inferior |
| `searchPlaceholder` | `string` | `"Buscar dirección..."` | Placeholder del buscador |
| `height` | `number` | `300` | Alto del mapa en px |

Características:
- Click en el mapa para colocar marcador
- Marcador arrastrable
- Búsqueda por dirección (Nominatim, limitado a España)
- Cambio automático de tiles (OSM light / CartoDB dark) según el tema
- Muestra coordenadas `lat, lng` en formato legible

```tsx
const [location, setLocation] = useState<{ lat: number; lng: number } | null>(null);

<LocationPicker
  value={location}
  onChange={setLocation}
  height={350}
/>
```

---

## search-dialog.tsx — `SearchDialog`

```tsx
import { SearchDialog, type SearchDialogProps } from "ruta/al/shared/ui/search-dialog";
```

Modal de búsqueda genérica con debounce, consulta remota y tabla de resultados. Genérico por tipo `T`.

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `open` | `boolean` | requerido | Control de visibilidad |
| `onOpenChange` | `(open: boolean) => void` | requerido | Callback de apertura/cierre |
| `title` | `string` | requerido | Título del diálogo |
| `placeholder` | `string` | — | Ejemplo de búsqueda |
| `columns` | `DataTableColumn<T>[]` | requerido | Columnas de resultados |
| `fetchFn` | `(query: string) => Promise<T[]>` | requerido | Función de búsqueda remota |
| `onSelect` | `(item: T) => void` | requerido | Callback al seleccionar |
| `getRowId` | `(item: T) => string` | — | Extrae ID de fila |
| `emptyMessage` | `string` | `"Sin resultados."` | Mensaje sin resultados |

```tsx
<SearchDialog<Customer>
  open={isOpen}
  onOpenChange={setIsOpen}
  title="Buscar cliente"
  placeholder="Nombre, NIF, teléfono..."
  columns={[
    { key: "name", header: "Cliente", render: (c) => c.commercial_name },
    { key: "doc", header: "NIF", render: (c) => c.document_number },
  ]}
  fetchFn={searchCustomers}
  onSelect={(customer) => handleSelect(customer)}
/>
```

---

## select.tsx — `Select`

```tsx
import { Select } from "ruta/al/shared/ui/select";
```

Select nativo estilizado. Para listas pequeñas (< 8 opciones). Para listas más grandes usar `Combobox`.

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `value` | `string` | requerido | Valor seleccionado |
| `onChange` | `(value: string) => void` | requerido | Callback al seleccionar |
| `options` | `{ value: string; label: string }[]` | requerido | Opciones |
| `placeholder` | `string` | — | Texto cuando no hay selección |

```tsx
<Select
  value={form.status}
  onChange={(val) => setForm({ ...form, status: val })}
  options={[
    { value: "active", label: "Activo" },
    { value: "inactive", label: "Inactivo" },
  ]}
  placeholder="Seleccionar estado"
/>
```

---

## theme-toggle.tsx — `ThemeToggle`

```tsx
import { ThemeToggle } from "ruta/al/shared/ui/theme-toggle";
```

Botón que alterna entre modo claro/oscuro. Usa `useThemeStore` de `features/theme/store`.

Sin props.

---

## checkbox.tsx — `Checkbox`

```tsx
import { Checkbox } from "ruta/al/shared/ui/checkbox";
```

Checkbox estilizado con acento del tema. Pasa todas las props de `InputHTMLAttributes`.

```tsx
<Checkbox checked={isActive} onChange={handleChange} />
```

---

## textarea.tsx — `Textarea`

```tsx
import { Textarea } from "ruta/al/shared/ui/textarea";
```

Campo de texto multilínea estilizado. Pasa todas las props de `TextareaHTMLAttributes`.

```tsx
<Textarea value={notes} onChange={handleChange} placeholder="Observaciones..." rows={3} />
```

---

## switch.tsx — `Switch`

```tsx
import { Switch } from "ruta/al/shared/ui/switch";
```

Toggle visual para campos booleanos. Renderiza un `<input type="checkbox">` oculto con un slider estilizado.

```tsx
<label className="flex items-center gap-2 text-sm">
  <Switch checked={isActive} onChange={handleChange} />
  Activo
</label>
```

---

## skeleton.tsx — `Skeleton`

```tsx
import { Skeleton } from "ruta/al/shared/ui/skeleton";
```

Placeholder animado para estados de carga. Útil combinado con `DataTable` o `Card`.

```tsx
<div className="space-y-2">
  <Skeleton className="h-4 w-3/4" />
  <Skeleton className="h-4 w-1/2" />
  <Skeleton className="h-4 w-full" />
</div>
```

---

## progress.tsx — `Progress`

```tsx
import { Progress } from "ruta/al/shared/ui/progress";
```

Barra de progreso con `role="progressbar"` accesible.

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `value` | `number` | requerido | Valor actual |
| `max` | `number` | `100` | Valor máximo |

```tsx
<Progress value={75} />
<Progress value={3} max={10} />
```

---

## breadcrumb.tsx — `Breadcrumb`

```tsx
import { Breadcrumb } from "ruta/al/shared/ui/breadcrumb";
```

Migas de pan para navegación jerárquica. El último item se muestra como texto, los anteriores como enlaces.

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `items` | `{ label: string; href?: string }[]` | requerido | Ruta de navegación |

```tsx
<Breadcrumb items={[
  { label: "Clientes", href: "/clientes" },
  { label: "Detalle", href: "/clientes/123" },
  { label: "Editar" },
]} />
```

---

## empty-state.tsx — `EmptyState`

```tsx
import { EmptyState } from "ruta/al/shared/ui/empty-state";
```

Estado vacío con icono, título, descripción opcional y acción.

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `title` | `string` | requerido | Título del estado vacío |
| `description` | `string` | — | Descripción opcional |
| `icon` | `ReactNode` | Icono por defecto | Icono personalizado |
| `action` | `ReactNode` | — | Botón o link de acción |

```tsx
<EmptyState
  title="No hay productos"
  description="Crea tu primer producto para empezar."
  action={<Button>Nuevo producto</Button>}
/>
```

---

## tabs.tsx — `Tabs`

```tsx
import { Tabs, type Tab } from "ruta/al/shared/ui/tabs";
```

Navegación por pestañas con contenido asociado.

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `value` | `string` | requerido | Pestaña activa |
| `onChange` | `(value: string) => void` | requerido | Callback al cambiar |
| `tabs` | `Tab[]` | requerido | `{ value, label, content, disabled? }` |

```tsx
<Tabs
  value={tab}
  onChange={setTab}
  tabs={[
    { value: "datos", label: "Datos generales", content: <DatosGenerales /> },
    { value: "bancos", label: "Datos bancarios", content: <BankAccountsSection /> },
    { value: "precios", label: "Precios especiales", content: <PricingTermsSection /> },
  ]}
/>
```

---

## pagination.tsx — `Pagination`

```tsx
import { Pagination } from "ruta/al/shared/ui/pagination";
```

Paginación con navegación numérica, elipsis y botones anterior/siguiente.

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `page` | `number` | requerido | Página actual |
| `totalPages` | `number` | requerido | Total de páginas |
| `onChange` | `(page: number) => void` | requerido | Callback al cambiar página |

```tsx
<Pagination page={page} totalPages={total} onChange={setPage} />
```

---

## tooltip.tsx — `Tooltip`

```tsx
import { Tooltip } from "ruta/al/shared/ui/tooltip";
```

Tooltip simple que aparece al hacer hover/focus. Se posiciona sobre el elemento.

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `content` | `string` | requerido | Texto del tooltip |
| `children` | `ReactNode` | requerido | Elemento trigger |

```tsx
<Tooltip content="Eliminar cliente">
  <Button onClick={handleDelete}>x</Button>
</Tooltip>
```

---

## popover.tsx — `Popover`

```tsx
import { Popover } from "ruta/al/shared/ui/popover";
```

Panel flotante que se abre al hacer click en el trigger. Se cierra al hacer click fuera.

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `trigger` | `ReactNode` | requerido | Elemento que abre el popover |
| `children` | `ReactNode` | requerido | Contenido del popover |
| `align` | `"start" \| "center" \| "end"` | `"center"` | Alineación horizontal |

```tsx
<Popover trigger={<Button>Filtros</Button>}>
  <div className="space-y-2 p-3">
    <label className="flex items-center gap-2 text-sm">
      <Checkbox /> Solo activos
    </label>
  </div>
</Popover>
```

---

## dropdown-menu.tsx — `DropdownMenu`

```tsx
import { DropdownMenu, type DropdownItem } from "ruta/al/shared/ui/dropdown-menu";
```

Menú contextual con acciones. Se cierra al hacer click fuera o al seleccionar un item.

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `trigger` | `ReactNode` | requerido | Elemento que abre el menú |
| `items` | `DropdownItem[]` | requerido | `{ label, onClick, disabled?, destructive?, icon? }` |
| `align` | `"start" \| "end"` | `"start"` | Alineación horizontal |

```tsx
<DropdownMenu
  trigger={<Button variant="secondary">Acciones</Button>}
  items={[
    { label: "Editar", onClick: () => handleEdit(row) },
    { label: "Duplicar", onClick: () => handleDuplicate(row) },
    { label: "Eliminar", onClick: () => handleDelete(row), destructive: true },
  ]}
/>
```

---

## toast.tsx — `ToastContainer`, `toast`

```tsx
import { toast, ToastContainer } from "ruta/al/shared/ui/toast";
```

Sistema de notificaciones global. Coloca `ToastContainer` una vez en el layout raíz y usa la función `toast()` desde cualquier componente.

```tsx
// En el layout:
<ToastContainer />

// En cualquier componente:
toast("Cliente guardado correctamente", "success");
toast("Error al guardar", "error");
toast("Procesando...", "info");
```

| Función | Tipo | Descripción |
|---------|------|-------------|
| `toast(message, type?)` | `(msg: string, type?: "success" \| "error" \| "info") => void` | Muestra notificación |
| `ToastContainer` | Componente | Renderiza las notificaciones (poner una vez en el root) |

---

## confirm-dialog.tsx — `ConfirmDialog`

```tsx
import { ConfirmDialog } from "ruta/al/shared/ui/confirm-dialog";
```

Diálogo de confirmación con acciones de confirmar/cancelar.

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `open` | `boolean` | requerido | Control de visibilidad |
| `onClose` | `() => void` | requerido | Callback al cerrar |
| `onConfirm` | `() => void` | requerido | Callback al confirmar |
| `title` | `string` | requerido | Título |
| `description` | `string` | — | Descripción opcional |
| `confirmLabel` | `string` | `"Confirmar"` | Texto del botón de confirmar |
| `cancelLabel` | `string` | `"Cancelar"` | Texto del botón de cancelar |
| `destructive` | `boolean` | `false` | Estilo rojo para acciones destructivas |
| `loading` | `boolean` | `false` | Estado de carga |

```tsx
<ConfirmDialog
  open={isOpen}
  onClose={() => setIsOpen(false)}
  onConfirm={handleDelete}
  title="Eliminar cliente"
  description="Esta acción no se puede deshacer."
  confirmLabel="Eliminar"
  destructive
/>
```

---

## file-upload.tsx — `FileUpload`

```tsx
import { FileUpload } from "ruta/al/shared/ui/file-upload";
```

Área de arrastrar y soltar archivos con click alternativo.

| Prop | Tipo | Default | Descripción |
|------|------|---------|-------------|
| `onFiles` | `(files: File[]) => void` | requerido | Callback con archivos seleccionados |
| `accept` | `string` | — | Tipos MIME aceptados (ej. `".png,.jpg"`) |
| `multiple` | `boolean` | `false` | Permitir múltiples archivos |
| `maxSize` | `number` | — | Tamaño máximo en bytes |

```tsx
<FileUpload
  onFiles={(files) => handleUpload(files)}
  accept=".pdf,.jpg"
  multiple
  maxSize={5 * 1024 * 1024}
/>
```

---

## cn.ts — `cn`

```tsx
import { cn } from "ruta/al/shared/ui/cn";
```

Wrapper de `clsx` + `tailwind-merge`. Útil para combinar clases condicionalmente.

```tsx
<div className={cn("base-class", condition && "extra-class", className)} />
```

---

## Domain wrappers (fuera de shared/ui)

### `apps/web/src/components/ProductSearchDialog.tsx`

Wrapper de `SearchDialog` para búsqueda de productos. Consulta `GET /api/v1/plugins/productos/products/search`.

```tsx
import { ProductSearchDialog, type ProductSearchDialogItem } from "ruta/a/components/ProductSearchDialog";

<ProductSearchDialog
  open={isOpen}
  onOpenChange={setIsOpen}
  onSelect={(product) => handleSelect(product)}
/>
```
