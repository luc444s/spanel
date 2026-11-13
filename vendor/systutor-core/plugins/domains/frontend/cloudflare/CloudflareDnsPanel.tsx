import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { ConfirmDialog } from "@systutor/shell/ui/confirm-dialog";
import { Input, noAutofillProps } from "@systutor/shell/ui/input";
import { toast } from "@systutor/shell/ui/toast";
import { t } from "@/i18n";

import {
  cloudflareKeys,
  createCloudflareRecord,
  deleteCloudflareRecord,
  listCloudflareRecords,
  updateCloudflareRecord,
} from "./api";
import { errorMessageFrom } from "./logic";
import type { CloudflareRecord, CloudflareRecordWrite, CloudflareZone } from "./types";

const RECORD_TYPES = ["A", "AAAA", "CNAME", "MX", "TXT", "CAA", "NS", "SRV"];
const PROXY_TYPES = new Set(["A", "AAAA", "CNAME"]);

type Draft = {
  name: string;
  type: string;
  content: string;
  ttl: string;
  proxied: boolean;
  priority: string;
};

const blankDraft = (zone: CloudflareZone): Draft => ({
  name: zone.name,
  type: "A",
  content: "",
  ttl: "300",
  proxied: false,
  priority: "",
});

const recordDraft = (record: CloudflareRecord): Draft => ({
  name: record.name,
  type: record.type,
  content: record.content,
  ttl: String(record.ttl || 1),
  proxied: record.proxied,
  priority: record.priority === null ? "" : String(record.priority),
});

/** Body of the unified DNS dialog when the zone belongs to Cloudflare. */
export function CloudflareDnsPanel({ zone }: { zone: CloudflareZone }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<CloudflareRecord | "new" | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [deleting, setDeleting] = useState<CloudflareRecord | null>(null);
  const [search, setSearch] = useState("");
  const [type, setType] = useState("");

  const recordsQuery = useQuery({
    queryKey: cloudflareKeys.records(zone.id),
    queryFn: () => listCloudflareRecords(zone.id),
    retry: false,
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: cloudflareKeys.records(zone.id) });
    await queryClient.invalidateQueries({ queryKey: cloudflareKeys.zones });
  };

  const saveMutation = useMutation({
    mutationFn: (payload: CloudflareRecordWrite) =>
      editing === "new"
        ? createCloudflareRecord(zone.id, payload)
        : updateCloudflareRecord(zone.id, (editing as CloudflareRecord).id, payload),
    onSuccess: async () => {
      toast.success(t(editing === "new" ? "DNS record created" : "DNS record updated"));
      setEditing(null);
      setDraft(null);
      await refresh();
    },
    onError: (failure: unknown) => toast.error(t(errorMessageFrom(failure))),
  });

  const deleteMutation = useMutation({
    mutationFn: (record: CloudflareRecord) =>
      deleteCloudflareRecord(zone.id, record.id),
    onSuccess: async () => {
      setDeleting(null);
      toast.success(t("DNS record deleted"));
      await refresh();
    },
    onError: (failure: unknown) => {
      setDeleting(null);
      toast.error(t(errorMessageFrom(failure)));
    },
  });

  const relativeName = (value: string) => {
    const name = value.replace(/\.$/, "").toLowerCase();
    if (name === zone.name.toLowerCase()) return "@";
    const suffix = `.${zone.name.toLowerCase()}`;
    return name.endsWith(suffix) ? name.slice(0, -suffix.length) : value;
  };

  const records = recordsQuery.data?.items ?? [];
  const types = useMemo(
    () => [...new Set(records.map((record) => record.type))].sort(),
    [records],
  );
  const needle = search.trim().toLowerCase();
  const filtered = records.filter((record) => (
    (!type || record.type === type)
    && (!needle
      || record.name.toLowerCase().includes(needle)
      || record.content.toLowerCase().includes(needle)
      || record.type.toLowerCase().includes(needle))
  ));

  const absoluteName = (value: string) => {
    const name = value.trim().replace(/\.$/, "");
    if (name === "@") return zone.name;
    const lower = name.toLowerCase();
    const suffix = `.${zone.name.toLowerCase()}`;
    return lower === zone.name.toLowerCase() || lower.endsWith(suffix)
      ? name
      : `${name}.${zone.name}`;
  };

  const save = () => {
    if (!draft) return;
    const ttl = Number(draft.ttl);
    const priority = draft.priority.trim() === "" ? undefined : Number(draft.priority);
    const recordPriority = Number.isFinite(priority) ? priority : undefined;
    if (!draft.name.trim() || !draft.content.trim() || !Number.isInteger(ttl) || ttl < 1) {
      toast.error(t("Name, content and a valid TTL are required."));
      return;
    }
    saveMutation.mutate({
      name: absoluteName(draft.name),
      type: draft.type,
      content: draft.content.trim(),
      ttl,
      proxied: PROXY_TYPES.has(draft.type) && draft.proxied,
      ...(recordPriority === undefined ? {} : { priority: recordPriority }),
    });
  };

  return <>
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Input className="min-w-52 flex-1" value={search}
          placeholder={t("Search name, type or value…")}
          onChange={(event) => setSearch(event.target.value)} />
        <select className="rounded-md border border-input bg-background px-2 text-sm"
          value={type} onChange={(event) => setType(event.target.value)}>
          <option value="">{t("All types")}</option>
          {types.map((value) => <option key={value}>{value}</option>)}
        </select>
        <Button size="sm" onClick={() => {
          setEditing("new");
          setDraft(blankDraft(zone));
        }}>{t("Add record")}</Button>
      </div>

      {recordsQuery.isError && <Alert title={t("Could not load DNS records")}>
        {t(errorMessageFrom(recordsQuery.error))}
      </Alert>}
      {recordsQuery.isLoading ? <p className="text-sm text-muted-foreground">
        {t("Loading DNS records...")}
      </p> : <div className="overflow-x-auto"><table className="w-full text-sm">
        <thead className="text-left text-xs text-muted-foreground"><tr>
          {["Name", "Type", "TTL", "Content", "Proxy", "Actions"].map((label) =>
            <th className="p-2" key={label}>{t(label)}</th>)}
        </tr></thead>
        <tbody>{filtered.map((record) =>
          <tr className="border-t border-border" key={record.id}>
            <td className="p-2">{relativeName(record.name)}</td>
            <td className="p-2">{record.type}</td>
            <td className="p-2">{record.ttl}</td>
            <td className="max-w-60 truncate p-2" title={record.content}>{record.content}</td>
            <td className="p-2">{record.proxied ? t("Yes") : t("No")}</td>
            <td className="p-2"><span className="flex gap-1">
              <Button size="sm" variant="secondary" onClick={() => {
                setEditing(record);
                setDraft(recordDraft(record));
              }}>{t("Edit")}</Button>
              <Button size="sm" variant="secondary" onClick={() => setDeleting(record)}>
                {t("Delete")}
              </Button>
            </span></td>
          </tr>)}</tbody>
      </table></div>}
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{filtered.length === records.length
          ? t("{count} records", { count: records.length })
          : t("Showing {count} of {total} records", {
            count: filtered.length, total: records.length,
          })}</span>
        <Button size="sm" variant="secondary"
          onClick={() => void recordsQuery.refetch()}>{t("Refresh")}</Button>
      </div>

      {draft && <div className="space-y-3 rounded-md border border-border p-3">
        <h3 className="font-medium">{t(editing === "new" ? "Add record" : "Edit record")}</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-sm">{t("Type")}<select value={draft.type}
            onChange={(event) => setDraft({ ...draft, type: event.target.value, proxied: false })}
            className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2">
            {RECORD_TYPES.map((type) => <option key={type}>{type}</option>)}</select></label>
          <label className="text-sm">{t("Name")}<Input value={draft.name}
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            {...noAutofillProps} /></label>
          <label className="text-sm sm:col-span-2">{t("Content")}<Input value={draft.content}
            onChange={(event) => setDraft({ ...draft, content: event.target.value })}
            spellCheck={false} {...noAutofillProps} /></label>
          <label className="text-sm">TTL<Input type="number" min={1} value={draft.ttl}
            onChange={(event) => setDraft({ ...draft, ttl: event.target.value })} /></label>
          {(draft.type === "MX" || draft.type === "SRV") && <label className="text-sm">
            {t("Priority")}<Input type="number" min={0} max={65535} value={draft.priority}
              onChange={(event) => setDraft({ ...draft, priority: event.target.value })} />
          </label>}
          {PROXY_TYPES.has(draft.type) && <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.proxied}
              onChange={(event) => setDraft({ ...draft, proxied: event.target.checked })} />
            {t("Proxy through Cloudflare")}
          </label>}
        </div>
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="secondary" onClick={() => {
            setEditing(null); setDraft(null);
          }}>{t("Cancel")}</Button>
          <Button size="sm" disabled={saveMutation.isPending} onClick={save}>
            {saveMutation.isPending ? t("Saving...") : t("Save")}
          </Button>
        </div>
      </div>}
    </div>

    <ConfirmDialog open={Boolean(deleting)} onClose={() => setDeleting(null)}
      onConfirm={() => deleting && deleteMutation.mutate(deleting)}
      title={t("Delete DNS record?")}
      description={deleting ? `${deleting.type} ${deleting.name}` : ""}
      confirmLabel={t("Delete")} cancelLabel={t("Cancel")} destructive
      loading={deleteMutation.isPending} />
  </>;
}
