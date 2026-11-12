import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { t } from "@/i18n";
import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { ConfirmDialog } from "@systutor/shell/ui/confirm-dialog";
import { Input } from "@systutor/shell/ui/input";
import { ApiError } from "@systutor/shell/api/client";
import { deleteRrset } from "./api";
import { filterRrsets, isLockedRrset, relativeOwner, safeErrorMessage } from "./logic";
import { RrsetEditor } from "./RrsetEditor";
import type { Rrset, RrsetDraft, Zone } from "./types";

const draftFrom = (zone: string, rrset: Rrset): RrsetDraft => ({
  name: relativeOwner(zone, rrset.name), type: rrset.type, ttl: rrset.ttl, values: rrset.records,
});

export function RecordsTab({ zone, canWrite, refresh }: {
  zone: Zone;
  canWrite: boolean;
  refresh: () => void;
}) {
  const [search, setSearch] = useState("");
  const [type, setType] = useState("");
  const [editing, setEditing] = useState<RrsetDraft | null | undefined>(undefined);
  const [deleting, setDeleting] = useState<Rrset | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const types = useMemo(() => [...new Set(zone.rrsets.map((item) => item.type))].sort(), [zone]);
  const filtered = filterRrsets(zone.rrsets, search, type);
  const mutation = useMutation({
    mutationFn: () => deleteRrset(zone.name, deleting!.name, deleting!.type, zone.version!),
    onSuccess: () => { setDeleteError(null); setDeleting(null); refresh(); },
    onError: (error) => {
      setDeleting(null);
      setDeleteError(error instanceof ApiError && error.status === 409
        ? t("The zone changed. Refresh it before deleting the RRset.")
        : safeErrorMessage(error));
    },
  });

  return <div className="space-y-3">
    <div className="flex flex-wrap gap-2">
      <Input className="min-w-52 flex-1" value={search} placeholder={t("Search name, type or value…")} onChange={(e) => setSearch(e.target.value)} />
      <select className="rounded-md border border-input bg-background px-2 text-sm" value={type} onChange={(e) => setType(e.target.value)}>
        <option value="">{t("All types")}</option>
        {types.map((value) => <option key={value}>{value}</option>)}
      </select>
      {canWrite && <Button size="sm" onClick={() => setEditing(null)}>{t("+ New RRset")}</Button>}
    </div>
    <div className="overflow-x-auto"><table className="w-full text-sm">
      <thead className="text-left text-xs text-muted-foreground"><tr>
        {["Name", "Type", "TTL", "Values", "Actions"].map((label) => <th className="p-2" key={label}>{t(label)}</th>)}
      </tr></thead>
      <tbody>{filtered.map((rrset) => {
        const locked = isLockedRrset(zone.name, rrset.name, rrset.type);
        return <tr className="border-t border-border" key={`${rrset.name}-${rrset.type}`}>
          <td className="p-2">{relativeOwner(zone.name, rrset.name)}</td><td className="p-2">{rrset.type}</td><td className="p-2">{rrset.ttl}</td>
          <td className="max-w-60 truncate p-2" title={rrset.type === "TXT" ? undefined : rrset.records.join(" · ")}>
            {rrset.type === "TXT" ? t("{count} values", { count: rrset.records.length }) : rrset.records.join(" · ")}
          </td>
          <td className="p-2">{locked ? <span title={t("Protected apex record")}>🔒</span> : canWrite && <span className="flex gap-1">
            <Button size="sm" variant="secondary" onClick={() => setEditing(draftFrom(zone.name, rrset))}>{t("Edit")}</Button>
            <Button size="sm" variant="secondary" onClick={() => { setDeleteError(null); setDeleting(rrset); }}>{t("Delete")}</Button>
          </span>}</td>
        </tr>;
      })}</tbody>
    </table></div>
    <div className="flex items-center justify-between text-xs text-muted-foreground">
      <span>{t("{count} RRsets · version {version}", { count: zone.rrsets.length, version: zone.version ?? "—" })}</span>
      <Button size="sm" variant="secondary" onClick={refresh}>{t("Refresh")}</Button>
    </div>
    <RrsetEditor zone={zone.name} initial={editing} onClose={() => setEditing(undefined)} onApplied={() => { setEditing(undefined); refresh(); }} />
    {deleteError && <Alert title={t("Could not delete RRset")}>{deleteError}</Alert>}
    <ConfirmDialog open={Boolean(deleting)} title={t("Delete {name} {type}?", { name: deleting?.name ?? "", type: deleting?.type ?? "" })}
      description={t("The complete RRset will be deleted.")} confirmLabel={t("Delete")} cancelLabel={t("Cancel")} destructive
      loading={mutation.isPending} onClose={() => { setDeleteError(null); setDeleting(null); }} onConfirm={() => mutation.mutate()} />
  </div>;
}
