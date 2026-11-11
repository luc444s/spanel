import { SearchDialog } from "@systutor/shell/ui/search-dialog";
import { apiRequest } from "@systutor/shell/api/client";

import { useT } from "../i18n/use-i18n";

export type ProductSearchDialogItem = {
  id: string;
  sku: string;
  name: string;
  brand_name: string | null;
  condition_code: string;
  is_active: boolean;
};

type ProductSearchDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (product: ProductSearchDialogItem) => void;
  title?: string;
};

function buildQuery(query: string) {
  const search = new URLSearchParams();
  search.set("q", query);
  search.set("limit", "20");
  return search.toString();
}

async function searchProducts(query: string): Promise<ProductSearchDialogItem[]> {
  if (!query.trim()) return [];
  return apiRequest(`/api/v1/plugins/productos/products/search?${buildQuery(query)}`);
}

export function ProductSearchDialog({
  open,
  onOpenChange,
  onSelect,
  title,
}: ProductSearchDialogProps) {
  const t = useT();

  return (
    <SearchDialog<ProductSearchDialogItem>
      open={open}
      onOpenChange={onOpenChange}
      title={title ?? t("Select product")}
      placeholder={t("GLP 10kg, SKU-001, 7750...")}
      fetchFn={searchProducts}
      onSelect={onSelect}
      getRowId={(item) => item.id}
      columns={[
        { key: "sku", header: t("SKU"), render: (row) => row.sku },
        { key: "name", header: t("Product"), render: (row) => row.name },
        { key: "brand", header: t("Brand"), render: (row) => row.brand_name ?? "-" },
        { key: "condition", header: t("Condition"), render: (row) => row.condition_code },
        { key: "status", header: t("Active"), render: (row) => (row.is_active ? t("Yes") : t("No")) },
      ]}
    />
  );
}
