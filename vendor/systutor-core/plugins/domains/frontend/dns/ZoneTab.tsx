import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { t } from "@/i18n";
import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { deleteZone, downloadZone, importZone } from "./api";
import { safeErrorMessage } from "./logic";
import type { Zone } from "./types";

export function ZoneTab({ zone, canRecords, canZones, onDeleted, refresh }: {
  zone: Zone;
  canRecords: boolean;
  canZones: boolean;
  onDeleted: () => void;
  refresh: () => void;
}) {
  const [content, setContent] = useState("");
  const [confirmImport, setConfirmImport] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const importing = useMutation({
    mutationFn: () => importZone(zone.name, content, zone.version!),
    onSuccess: () => { setError(null); setConfirmImport(false); setContent(""); refresh(); },
    onError: (reason) => setError(safeErrorMessage(reason)),
  });
  const deleting = useMutation({
    mutationFn: () => deleteZone(zone.name, zone.version!),
    onSuccess: () => { setError(null); onDeleted(); },
    onError: (reason) => setError(safeErrorMessage(reason)),
  });

  return <div className="space-y-5">
    <section className="space-y-2"><h3 className="font-medium">{t("Export")}</h3>
      <p className="text-sm text-muted-foreground">{t("Download the deterministic zone file.")}</p>
      <Button size="sm" variant="secondary" onClick={() => void downloadZone(zone.name).catch((reason) => setError(safeErrorMessage(reason)))}>{t("Download .zone")}</Button>
    </section>
    {canRecords && <section className="space-y-2 border-t border-border pt-4"><h3 className="font-medium">{t("Import")}</h3>
      <input type="file" accept=".zone,text/plain" onChange={(event) => {
        const file = event.target.files?.[0];
        if (file) void file.text().then(setContent);
      }} />
      <textarea className="min-h-32 w-full rounded-md border border-input bg-background p-2 font-mono text-sm" value={content}
        placeholder={t("Paste zone-file content")} onChange={(event) => setContent(event.target.value)} />
      <Button size="sm" disabled={!content.trim()} onClick={() => setConfirmImport(true)}>{t("Import zone")}</Button>
    </section>}
    {canZones && <section className="space-y-2 border-t border-border pt-4"><h3 className="font-medium text-destructive">{t("Delete zone")}</h3>
      <p className="text-sm text-muted-foreground">{t("Type the zone name to confirm:")}</p>
      <Input value={typed} onChange={(event) => setTyped(event.target.value)} />
      <Button disabled={typed !== zone.name} onClick={() => setConfirmDelete(true)}>{t("Delete zone")}</Button>
    </section>}
    {error && <Alert title={t("DNS operation failed")}>{error}</Alert>}
    <Dialog open={confirmImport} title={t("Import zone {zone}?", { zone: zone.name })} onClose={() => setConfirmImport(false)}>
      <p className="text-sm text-muted-foreground">{t("The imported file replaces the current record set using the current zone version.")}</p>
      <div className="mt-4 flex justify-end gap-2"><Button variant="secondary" onClick={() => setConfirmImport(false)}>{t("Cancel")}</Button>
        <Button disabled={importing.isPending} onClick={() => importing.mutate()}>{t("Import zone")}</Button></div>
    </Dialog>
    <Dialog open={confirmDelete} title={t("Delete zone {zone}?", { zone: zone.name })} onClose={() => setConfirmDelete(false)}>
      <p className="text-sm text-muted-foreground">{t("This deletes the authoritative zone and releases its ownership.")}</p>
      <div className="mt-4 flex justify-end gap-2"><Button variant="secondary" onClick={() => setConfirmDelete(false)}>{t("Cancel")}</Button>
        <Button disabled={deleting.isPending} onClick={() => deleting.mutate()}>{t("Delete zone")}</Button></div>
    </Dialog>
  </div>;
}
