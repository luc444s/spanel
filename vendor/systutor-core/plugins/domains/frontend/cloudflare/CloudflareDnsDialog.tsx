import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { ConfirmDialog } from "@systutor/shell/ui/confirm-dialog";
import { Dialog } from "@systutor/shell/ui/dialog";
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

export function CloudflareDnsDialog({ zone, onClose }: {
  zone: CloudflareZone | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<CloudflareRecord | "new" | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [deleting, setDeleting] = useState<CloudflareRecord | null>(null);

  const recordsQuery = useQuery({
    queryKey: cloudflareKeys.records(zone?.id ?? ""),
    queryFn: () => listCloudflareRecords(zone?.id ?? ""),
    enabled: Boolean(zone),
    retry: false,
  });

  const refresh = async () => {
    if (!zone) return;
    await queryClient.invalidateQueries({ queryKey: cloudflareKeys.records(zone.id) });
    await queryClient.invalidateQueries({ queryKey: cloudflareKeys.zones });
  };

  const saveMutation = useMutation({
    mutationFn: async (payload: CloudflareRecordWrite) => {
      if (!zone) throw new Error("Cloudflare zone is unavailable");
      return editing === "new"
        ? createCloudflareRecord(zone.id, payload)
        : updateCloudflareRecord(zone.id, (editing as CloudflareRecord).id, payload);
    },
    onSuccess: async () => {
      toast.success(t(editing === "new" ? "DNS record created" : "DNS record updated"));
      setEditing(null);
      setDraft(null);
      await refresh();
    },
    onError: (failure: unknown) => toast.error(t(errorMessageFrom(failure))),
  });

  const deleteMutation = useMutation({
    mutationFn: async (record: CloudflareRecord) => {
      if (!zone) throw new Error("Cloudflare zone is unavailable");
      return deleteCloudflareRecord(zone.id, record.id);
    },
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

  const close = () => {
    setEditing(null);
    setDraft(null);
    setDeleting(null);
    onClose();
  };

  const absoluteName = (value: string) => {
    const name = value.trim().replace(/\.$/, "");
    if (!zone || name === "@") return zone?.name ?? name;
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
    <Dialog open={Boolean(zone)} onClose={close}
      title={zone ? `Cloudflare DNS · ${zone.name}` : "Cloudflare DNS"}
      description={t("Records are changed directly in the connected Cloudflare account.")}
      maxWidthClassName="max-w-5xl">
      <div className="space-y-4">
        <div className="flex justify-end">
          <Button size="sm" onClick={() => {
            if (!zone) return;
            setEditing("new");
            setDraft(blankDraft(zone));
          }}>{t("Add record")}</Button>
        </div>

        {recordsQuery.isError && <Alert title={t("Could not load DNS records")}>
          {t(errorMessageFrom(recordsQuery.error))}
        </Alert>}
        {recordsQuery.isLoading ? <p className="text-sm text-muted-foreground">
          {t("Loading DNS records...")}
        </p> : <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full min-w-[720px] text-sm">
            <thead className="bg-muted/70 text-left text-xs text-muted-foreground">
              <tr><th className="px-3 py-2">{t("Type")}</th>
                <th className="px-3 py-2">{t("Name")}</th>
                <th className="px-3 py-2">{t("Content")}</th>
                <th className="px-3 py-2">TTL</th>
                <th className="px-3 py-2">{t("Proxy")}</th>
                <th className="px-3 py-2 text-right">{t("Actions")}</th></tr>
            </thead>
            <tbody>{(recordsQuery.data?.items ?? []).map((record) =>
              <tr key={record.id} className="border-t border-border/70">
                <td className="px-3 py-2 font-medium">{record.type}</td>
                <td className="max-w-48 truncate px-3 py-2" title={record.name}>{record.name}</td>
                <td className="max-w-72 truncate px-3 py-2 font-mono text-xs"
                  title={record.content}>{record.content}</td>
                <td className="px-3 py-2">{record.ttl}</td>
                <td className="px-3 py-2">{record.proxied ? t("Yes") : t("No")}</td>
                <td className="px-3 py-2"><div className="flex justify-end gap-1">
                  <Button size="sm" variant="secondary" onClick={() => {
                    setEditing(record);
                    setDraft(recordDraft(record));
                  }}>{t("Edit")}</Button>
                  <Button size="sm" variant="secondary" onClick={() => setDeleting(record)}>
                    {t("Delete")}
                  </Button>
                </div></td>
              </tr>)}</tbody>
          </table>
          {!recordsQuery.data?.items.length && !recordsQuery.isError
            && <p className="p-4 text-sm text-muted-foreground">{t("No DNS records")}</p>}
        </div>}

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
    </Dialog>

    <ConfirmDialog open={Boolean(deleting)} onClose={() => setDeleting(null)}
      onConfirm={() => deleting && deleteMutation.mutate(deleting)}
      title={t("Delete DNS record?")}
      description={deleting ? `${deleting.type} ${deleting.name}` : ""}
      confirmLabel={t("Delete")} cancelLabel={t("Cancel")} destructive
      loading={deleteMutation.isPending} />
  </>;
}
