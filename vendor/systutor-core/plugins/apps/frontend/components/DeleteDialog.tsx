import { useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";

import { useT } from "@/i18n/use-i18n";

import { deleteApplication, newIdempotencyKey } from "../api";
import type { Application, OperationSummary } from "../types";

export function DeleteDialog({ application, open, onClose, onAccepted }: {
  application: Application; open: boolean; onClose: () => void;
  onAccepted: (operation: OperationSummary) => void;
}) {
  const t = useT();
  const [confirmation, setConfirmation] = useState("");
  const [purge, setPurge] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit() {
    setPending(true); setError(null);
    try {
      const operation = await deleteApplication(application.id, confirmation, purge, newIdempotencyKey());
      setConfirmation(""); setPurge(false); onAccepted(operation);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("Could not accept the deletion"));
    } finally { setPending(false); }
  }
  return <Dialog open={open} title={t("Delete application")} onClose={onClose} maxWidthClassName="max-w-md">
    <div className="space-y-3 text-sm">
      <Alert title={t("Destructive action")}>{t("Success is only confirmed when the job reaches a terminal state.")}</Alert>
      <p>{t("Type")} <code>{application.slug}</code> {t("to confirm.")}</p>
      <Input value={confirmation} onChange={(e) => setConfirmation(e.target.value)} />
      <label className="flex gap-2"><input type="checkbox" checked={purge} onChange={(e) => setPurge(e.target.checked)} /> {t("Also purge persistent data")}</label>
      {error ? <Alert title={t("Error")}>{error}</Alert> : null}
      <div className="flex justify-end gap-2"><Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button><Button disabled={pending || confirmation !== application.slug} onClick={() => void submit()}>{t("Delete")}</Button></div>
    </div>
  </Dialog>;
}
