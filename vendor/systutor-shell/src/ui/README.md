# Core UI Components

English | [Español](README.es.md)

Reusable core frontend components. They live in `apps/web/src/shared/ui/` and can be imported from any plugin or page.

Style: utility-first with Tailwind CSS, semantic colors via CSS variables, no hardcoded colors.

---

## Form patterns (mandatory — read before creating any form)

### Label + input

```tsx
<label className="block space-y-2 text-sm text-foreground">
  <span>Field name</span>
  <Input value={val} onChange={fn} />
  {/* or <Combobox />, <Select />, <Textarea /> */}
</label>
```

Never: `text-xs`, `text-muted-foreground` on labels, red asterisks for required.

### Form layout

```tsx
<form className="space-y-6" onSubmit={handleSubmit}>
  {error && <Alert title="Error">{error}</Alert>}

  <div className="grid gap-4 md:grid-cols-2">
    <label className="block space-y-2 text-sm text-foreground">...</label>
    <label className="block space-y-2 text-sm text-foreground">...</label>
  </div>

  <div className="flex justify-end gap-3">
    <Button type="button" variant="secondary">Cancel</Button>
    <Button type="submit">Save</Button>
  </div>
</form>
```

### Which component to use

| Need | Component | Import |
|-----------|-----------|--------|
| Text/date input | `Input` | `shared/ui/input` |
| Multiline | `Textarea` | `shared/ui/input` |
| Searchable select | `Combobox` | `shared/ui/combobox` |
| Simple select | `Select` | `shared/ui/select` |
| Button | `Button` | `shared/ui/button` |
| Error | `Alert` | `shared/ui/alert` |
| Modal | `Dialog` | `shared/ui/dialog` |
| Checkbox | `Checkbox` | `shared/ui/input` |

### Forbidden

- `<button>` with inline styles → use `<Button>`
- Native `<textarea>` → use `<Textarea>`
- Red divs for errors → use `<Alert>`
- Red `*` asterisks on labels
- `text-xs` or `text-muted-foreground` on labels
- `style={{}}` or hardcoded colors

---

## alert.tsx — `Alert`

```tsx
import { Alert } from "ruta/al/shared/ui/alert";
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `title` | `string` | required | Message title |
| `children` | `ReactNode` | — | Descriptive content |

---

## badge.tsx — `Badge`

```tsx
import { Badge } from "ruta/al/shared/ui/badge";
```

Generic badge. Accepts `className` to customize the status color.

```tsx
<Badge className="bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300">Active</Badge>
```

---

## button.tsx — `Button`

```tsx
import { Button } from "ruta/al/shared/ui/button";
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `variant` | `"primary" \| "secondary"` | `"primary"` | Visual style |
| `...props` | `ButtonHTMLAttributes` | — | Passed to the `<button>` element |

```tsx
<Button>Save</Button>
<Button variant="secondary">Cancel</Button>
<Button disabled>Sending...</Button>
```

---

## card.tsx — `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardContent`

```tsx
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "ruta/al/shared/ui/card";
```

```tsx
<Card>
  <CardHeader>
    <CardTitle>Title</CardTitle>
    <CardDescription>Subtitle or description</CardDescription>
  </CardHeader>
  <CardContent>Card content</CardContent>
</Card>
```

---

## combobox.tsx — `Combobox`

```tsx
import { Combobox, type ComboboxOption } from "ruta/al/shared/ui/combobox";
```

Select with an inline search box. Recommended when there are more than 5-8 options.

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `value` | `string` | required | Selected value |
| `onChange` | `(value: string) => void` | required | Callback on select |
| `options` | `ComboboxOption[]` | required | `{ value, label, keywords? }` |
| `placeholder` | `string` | — | Text when nothing is selected |
| `searchPlaceholder` | `string` | `"Search..."` | Search input placeholder |
| `emptyMessage` | `string` | `"No options."` | Message when there are no results |
| `disabled` | `boolean` | `false` | Disabled |

```tsx
<Combobox
  value={form.type}
  onChange={(val) => setForm({ ...form, type: val })}
  options={[
    { value: "GLOBAL", label: "Global", keywords: ["global"] },
    { value: "PRODUCT", label: "Product", keywords: ["product"] },
  ]}
  placeholder="Select type"
/>
```

---

## data-table.tsx — `DataTable`

```tsx
import { DataTable, type DataTableColumn } from "ruta/al/shared/ui/data-table";
```

Generic table with clickable row support and dense mode.

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `columns` | `DataTableColumn<Row>[]` | required | `{ key, header, render, className? }` |
| `rows` | `Row[]` | required | Table data |
| `rowKey` | `(row: Row) => string` | required | Function returning the unique key |
| `emptyMessage` | `string` | required | Message when there are no rows |
| `onRowClick` | `(row: Row) => void` | — | Callback when a row is clicked |
| `dense` | `boolean` | `false` | Reduces cell padding |

```tsx
<DataTable
  columns={[
    { key: "name", header: "Name", render: (row) => row.name },
    { key: "email", header: "Email", render: (row) => row.email ?? "-" },
  ]}
  rows={data}
  rowKey={(row) => row.id}
  emptyMessage="No records."
  onRowClick={(row) => handleEdit(row)}
/>
```

---

## dialog.tsx — `Dialog`

```tsx
import { Dialog } from "ruta/al/shared/ui/dialog";
```

Basic modal. Closes when clicking outside or on "Close".

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `open` | `boolean` | required | Visibility control |
| `title` | `string` | required | Modal title |
| `description` | `string` | — | Optional subtitle |
| `children` | `ReactNode` | required | Modal content |
| `actions` | `ReactNode` | — | Action footer |
| `onClose` | `() => void` | required | Callback on close |
| `maxWidthClassName` | `string` | `"max-w-2xl"` | Tailwind class for max width |

```tsx
<Dialog open={isOpen} title="New customer" onClose={() => setIsOpen(false)}>
  <form>...</form>
</Dialog>
```

---

## input.tsx — `Input`

```tsx
import { Input } from "ruta/al/shared/ui/input";
```

`<input>` wrapper with system styles. Passes all standard HTMLInputElement props.

```tsx
<Input value={name} onChange={handleChange} placeholder="Customer name" />
<Input type="date" value={date} onChange={handleDateChange} />
```

---

## location-picker.tsx — `LocationPicker`

```tsx
import { LocationPicker } from "ruta/al/shared/ui/location-picker";
```

Geographic location picker with an interactive map (Leaflet + OpenStreetMap). Includes address search via Nominatim.

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `value` | `{ lat: number; lng: number } \| null` | required | Current location |
| `onChange` | `(location: { lat: number; lng: number }) => void` | required | Callback when the location changes |
| `className` | `string` | — | Additional class |
| `placeholder` | `string` | `"Click the map to select a location"` | Bottom text |
| `searchPlaceholder` | `string` | `"Search address..."` | Search box placeholder |
| `height` | `number` | `300` | Map height in px |

Features:
- Click the map to place the marker
- Draggable marker
- Address search (Nominatim, limited to Spain)
- Automatic tile switching (OSM light / CartoDB dark) based on the theme
- Shows `lat, lng` coordinates in a readable format

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

Generic search modal with debounce, remote query and results table. Generic over type `T`.

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `open` | `boolean` | required | Visibility control |
| `onOpenChange` | `(open: boolean) => void` | required | Open/close callback |
| `title` | `string` | required | Dialog title |
| `placeholder` | `string` | — | Search example |
| `columns` | `DataTableColumn<T>[]` | required | Result columns |
| `fetchFn` | `(query: string) => Promise<T[]>` | required | Remote search function |
| `onSelect` | `(item: T) => void` | required | Callback on select |
| `getRowId` | `(item: T) => string` | — | Extracts the row ID |
| `emptyMessage` | `string` | `"No results."` | No-results message |

```tsx
<SearchDialog<Customer>
  open={isOpen}
  onOpenChange={setIsOpen}
  title="Search customer"
  placeholder="Name, tax ID, phone..."
  columns={[
    { key: "name", header: "Customer", render: (c) => c.commercial_name },
    { key: "doc", header: "Tax ID", render: (c) => c.document_number },
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

Styled native select. For small lists (< 8 options). For larger lists use `Combobox`.

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `value` | `string` | required | Selected value |
| `onChange` | `(value: string) => void` | required | Callback on select |
| `options` | `{ value: string; label: string }[]` | required | Options |
| `placeholder` | `string` | — | Text when nothing is selected |

```tsx
<Select
  value={form.status}
  onChange={(val) => setForm({ ...form, status: val })}
  options={[
    { value: "active", label: "Active" },
    { value: "inactive", label: "Inactive" },
  ]}
  placeholder="Select status"
/>
```

---

## theme-toggle.tsx — `ThemeToggle`

```tsx
import { ThemeToggle } from "ruta/al/shared/ui/theme-toggle";
```

Button that toggles between light/dark mode. Uses `useThemeStore` from `features/theme/store`.

No props.

---

## checkbox.tsx — `Checkbox`

```tsx
import { Checkbox } from "ruta/al/shared/ui/checkbox";
```

Checkbox styled with the theme accent. Passes all `InputHTMLAttributes` props.

```tsx
<Checkbox checked={isActive} onChange={handleChange} />
```

---

## textarea.tsx — `Textarea`

```tsx
import { Textarea } from "ruta/al/shared/ui/textarea";
```

Styled multiline text field. Passes all `TextareaHTMLAttributes` props.

```tsx
<Textarea value={notes} onChange={handleChange} placeholder="Notes..." rows={3} />
```

---

## switch.tsx — `Switch`

```tsx
import { Switch } from "ruta/al/shared/ui/switch";
```

Visual toggle for boolean fields. Renders a hidden `<input type="checkbox">` with a styled slider.

```tsx
<label className="flex items-center gap-2 text-sm">
  <Switch checked={isActive} onChange={handleChange} />
  Active
</label>
```

---

## skeleton.tsx — `Skeleton`

```tsx
import { Skeleton } from "ruta/al/shared/ui/skeleton";
```

Animated placeholder for loading states. Useful combined with `DataTable` or `Card`.

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

Progress bar with an accessible `role="progressbar"`.

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `value` | `number` | required | Current value |
| `max` | `number` | `100` | Maximum value |

```tsx
<Progress value={75} />
<Progress value={3} max={10} />
```

---

## breadcrumb.tsx — `Breadcrumb`

```tsx
import { Breadcrumb } from "ruta/al/shared/ui/breadcrumb";
```

Breadcrumbs for hierarchical navigation. The last item is shown as text, the previous ones as links.

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `items` | `{ label: string; href?: string }[]` | required | Navigation path |

```tsx
<Breadcrumb items={[
  { label: "Customers", href: "/customers" },
  { label: "Detail", href: "/customers/123" },
  { label: "Edit" },
]} />
```

---

## empty-state.tsx — `EmptyState`

```tsx
import { EmptyState } from "ruta/al/shared/ui/empty-state";
```

Empty state with an icon, title, optional description and action.

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `title` | `string` | required | Empty state title |
| `description` | `string` | — | Optional description |
| `icon` | `ReactNode` | Default icon | Custom icon |
| `action` | `ReactNode` | — | Action button or link |

```tsx
<EmptyState
  title="No products"
  description="Create your first product to get started."
  action={<Button>New product</Button>}
/>
```

---

## tabs.tsx — `Tabs`

```tsx
import { Tabs, type Tab } from "ruta/al/shared/ui/tabs";
```

Tab navigation with associated content.

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `value` | `string` | required | Active tab |
| `onChange` | `(value: string) => void` | required | Callback on change |
| `tabs` | `Tab[]` | required | `{ value, label, content, disabled? }` |

```tsx
<Tabs
  value={tab}
  onChange={setTab}
  tabs={[
    { value: "datos", label: "General data", content: <DatosGenerales /> },
    { value: "bancos", label: "Bank details", content: <BankAccountsSection /> },
    { value: "precios", label: "Special prices", content: <PricingTermsSection /> },
  ]}
/>
```

---

## pagination.tsx — `Pagination`

```tsx
import { Pagination } from "ruta/al/shared/ui/pagination";
```

Pagination with numeric navigation, ellipsis and previous/next buttons.

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `page` | `number` | required | Current page |
| `totalPages` | `number` | required | Total pages |
| `onChange` | `(page: number) => void` | required | Callback on page change |

```tsx
<Pagination page={page} totalPages={total} onChange={setPage} />
```

---

## tooltip.tsx — `Tooltip`

```tsx
import { Tooltip } from "ruta/al/shared/ui/tooltip";
```

Simple tooltip that appears on hover/focus. Positioned above the element.

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `content` | `string` | required | Tooltip text |
| `children` | `ReactNode` | required | Trigger element |

```tsx
<Tooltip content="Delete customer">
  <Button onClick={handleDelete}>x</Button>
</Tooltip>
```

---

## popover.tsx — `Popover`

```tsx
import { Popover } from "ruta/al/shared/ui/popover";
```

Floating panel that opens when the trigger is clicked. Closes when clicking outside.

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `trigger` | `ReactNode` | required | Element that opens the popover |
| `children` | `ReactNode` | required | Popover content |
| `align` | `"start" \| "center" \| "end"` | `"center"` | Horizontal alignment |

```tsx
<Popover trigger={<Button>Filters</Button>}>
  <div className="space-y-2 p-3">
    <label className="flex items-center gap-2 text-sm">
      <Checkbox /> Active only
    </label>
  </div>
</Popover>
```

---

## dropdown-menu.tsx — `DropdownMenu`

```tsx
import { DropdownMenu, type DropdownItem } from "ruta/al/shared/ui/dropdown-menu";
```

Context menu with actions. Closes when clicking outside or when an item is selected.

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `trigger` | `ReactNode` | required | Element that opens the menu |
| `items` | `DropdownItem[]` | required | `{ label, onClick, disabled?, destructive?, icon? }` |
| `align` | `"start" \| "end"` | `"start"` | Horizontal alignment |

```tsx
<DropdownMenu
  trigger={<Button variant="secondary">Actions</Button>}
  items={[
    { label: "Edit", onClick: () => handleEdit(row) },
    { label: "Duplicate", onClick: () => handleDuplicate(row) },
    { label: "Delete", onClick: () => handleDelete(row), destructive: true },
  ]}
/>
```

---

## toast.tsx — `ToastContainer`, `toast`

```tsx
import { toast, ToastContainer } from "ruta/al/shared/ui/toast";
```

Global notification system. Place `ToastContainer` once in the root layout and use the `toast()` function from any component.

```tsx
// In the layout:
<ToastContainer />

// In any component:
toast("Customer saved successfully", "success");
toast("Error while saving", "error");
toast("Processing...", "info");
```

| Function | Type | Description |
|---------|------|-------------|
| `toast(message, type?)` | `(msg: string, type?: "success" \| "error" \| "info") => void` | Shows a notification |
| `ToastContainer` | Component | Renders the notifications (place once in the root) |

---

## confirm-dialog.tsx — `ConfirmDialog`

```tsx
import { ConfirmDialog } from "ruta/al/shared/ui/confirm-dialog";
```

Confirmation dialog with confirm/cancel actions.

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `open` | `boolean` | required | Visibility control |
| `onClose` | `() => void` | required | Callback on close |
| `onConfirm` | `() => void` | required | Callback on confirm |
| `title` | `string` | required | Title |
| `description` | `string` | — | Optional description |
| `confirmLabel` | `string` | `"Confirm"` | Confirm button text |
| `cancelLabel` | `string` | `"Cancel"` | Cancel button text |
| `destructive` | `boolean` | `false` | Red style for destructive actions |
| `loading` | `boolean` | `false` | Loading state |

```tsx
<ConfirmDialog
  open={isOpen}
  onClose={() => setIsOpen(false)}
  onConfirm={handleDelete}
  title="Delete customer"
  description="This action cannot be undone."
  confirmLabel="Delete"
  destructive
/>
```

---

## file-upload.tsx — `FileUpload`

```tsx
import { FileUpload } from "ruta/al/shared/ui/file-upload";
```

Drag-and-drop file area with a click alternative.

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `onFiles` | `(files: File[]) => void` | required | Callback with the selected files |
| `accept` | `string` | — | Accepted MIME types (e.g. `".png,.jpg"`) |
| `multiple` | `boolean` | `false` | Allow multiple files |
| `maxSize` | `number` | — | Maximum size in bytes |

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

`clsx` + `tailwind-merge` wrapper. Useful for combining classes conditionally.

```tsx
<div className={cn("base-class", condition && "extra-class", className)} />
```

---

## Domain wrappers (outside shared/ui)

### `apps/web/src/components/ProductSearchDialog.tsx`

Wrapper around `SearchDialog` for product search. Queries `GET /api/v1/plugins/productos/products/search`.

```tsx
import { ProductSearchDialog, type ProductSearchDialogItem } from "ruta/a/components/ProductSearchDialog";

<ProductSearchDialog
  open={isOpen}
  onOpenChange={setIsOpen}
  onSelect={(product) => handleSelect(product)}
/>
```
