import { FormEvent, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";

import { useT } from "@/i18n/use-i18n";

export function SecretRevealDialog({ name, pending, error, onClose, onReveal }: {
  name: string | null;
  pending: boolean;
  error: string | null;
  onClose: () => void;
  onReveal: (reason: string) => void;
}) {
  const t = useT();
  const [reason, setReason] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    if (reason.trim()) onReveal(reason.trim());
  }
  return <Dialog open={Boolean(name)} title={t("Reveal {name}", { name: name ?? t("secret") })} onClose={onClose}>
    <form className="space-y-3" onSubmit={submit}>
      <p className="text-sm">{t("This action will be recorded in the audit log.")}</p>
      <p className="text-sm text-muted-foreground">{t("The value may grant access to application data. Do not share it or include it in logs.")}</p>
      <label className="block space-y-1 text-sm"><span>{t("Reason *")}</span><Input value={reason} maxLength={500} autoFocus onChange={(event) => setReason(event.target.value)} /></label>
      {error ? <Alert title={t("Could not reveal")}>{error}</Alert> : null}
      <div className="flex justify-end gap-2"><Button type="button" variant="secondary" onClick={onClose}>{t("Cancel")}</Button><Button type="submit" disabled={pending || !reason.trim()}>{t("Reveal for 30 s")}</Button></div>
    </form>
  </Dialog>;
}
