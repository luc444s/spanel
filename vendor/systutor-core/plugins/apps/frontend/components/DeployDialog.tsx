import { useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input, Textarea } from "@systutor/shell/ui/input";
import { toast } from "@systutor/shell/ui/toast";
import { useT } from "@/i18n/use-i18n";

import { deployApplication, newIdempotencyKey } from "../api";
import { parseDotenv } from "../logic";
import type { Application, EnvironmentEntry, OperationSummary } from "../types";

export function DeployDialog({ application, open, blocked, onClose, onAccepted }: {
  application: Application;
  open: boolean;
  blocked: boolean;
  onClose: () => void;
  onAccepted: (operation: OperationSummary) => void;
}) {
  const t = useT();
  const [dotenv, setDotenv] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [intent, setIntent] = useState<{ key: string; body: EnvironmentEntry[] } | null>(null);

  async function submit() {
    if (blocked || confirmation !== application.slug) return;
    let request = intent;
    if (!request) {
      try {
        request = { key: newIdempotencyKey(), body: parseDotenv(dotenv) };
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : t("The .env block is not valid"));
        return;
      }
    }
    setIntent(request);
    setPending(true); setError(null);
    const toastId = `apps-deploy-${application.id}`;
    toast.loading(t("Sending deployment…"), { id: toastId });
    try {
      const operation = await deployApplication(application.id, request.body, request.key);
      // Secrets are destroyed immediately after the server accepts the job.
      setDotenv(""); setConfirmation(""); setIntent(null);
      toast.success(t("Deployment accepted; follow progress in Operations"), { id: toastId });
      onAccepted(operation);
    } catch (reason) {
      // Keep the same in-memory key/body only for an explicit retry of this uncertain request.
      const message = reason instanceof Error ? reason.message : t("Could not accept the deployment");
      setError(message);
      toast.error(message, { id: toastId });
    } finally {
      setPending(false);
    }
  }

  function close() {
    setDotenv(""); setConfirmation(""); setIntent(null); setError(null);
    onClose();
  }

  return <Dialog open={open} title={t("Deploy {name}", { name: application.name })} onClose={close}>
    <div className="space-y-4">
      <Alert title={t("Untrusted code")}>{t("Docker build will run code from the repository. Review the audit before continuing.")}</Alert>
      <dl className="grid grid-cols-2 gap-2 text-sm">
        <div><dt className="text-muted-foreground">{t("Domain")}</dt><dd>{application.domain ?? t("Not published")}</dd></div>
        <div><dt className="text-muted-foreground">{t("Ingress")}</dt><dd>{application.ingress_service ? `${application.ingress_service}:${application.ingress_port}` : t("No ingress")}</dd></div>
      </dl>
      <div className="space-y-2 rounded-md border border-border p-3">
        <label className="block space-y-2 text-sm">
          <span className="font-medium">{t("Variables in .env format")}</span>
          <Textarea
            className="min-h-48 font-mono text-xs"
            value={dotenv}
            onChange={(event) => { setDotenv(event.target.value); setIntent(null); setError(null); }}
            placeholder={application.has_saved_environment
              ? t("Leave empty to reuse the saved variables, or paste a new block to replace them.")
              : "DATABASE_URL=protocol://${APP_DB_USER}:${APP_DB_PASSWORD}@database:1234/${APP_DB_NAME}\nVITE_API_BASE_URL=https://api.example"}
            autoComplete="off"
            spellCheck={false}
          />
        </label>
        {application.has_saved_environment ? <p className="text-xs text-muted-foreground">
          {t("This deployment has saved variables. If you leave the field empty they are reused; if you paste a block, it replaces the previous ones. Values are not shown again.")}
        </p> : <p className="text-xs text-muted-foreground">
          {t("You can paste a complete .env file. When accepted, the block is stored encrypted for future deployments.")}
        </p>}
        <p className="text-xs text-muted-foreground">
          {t("Apps generates and keeps APP_DB_NAME, APP_DB_USER and APP_DB_PASSWORD encrypted. Do not include them here; Compose can reference them from any engine.")}
        </p>
      </div>
      <label className="block space-y-1 text-sm"><span>{t("Type {slug} to confirm", { slug: application.slug })}</span><Input value={confirmation} onChange={(e) => setConfirmation(e.target.value)} /></label>
      {blocked ? <Alert title={t("Deployment blocked")}>{t("The latest audit contains errors. There is no bypass.")}</Alert> : null}
      {error ? <Alert title={t("Error")}>{error}</Alert> : null}
      <div className="flex justify-end gap-2">
        <Button variant="secondary" disabled={pending} onClick={close}>{t("Cancel")}</Button>
        <Button disabled={pending || blocked || confirmation !== application.slug} onClick={() => void submit()}>{pending ? t("Accepting…") : intent ? t("Retry same request") : t("Deploy")}</Button>
      </div>
    </div>
  </Dialog>;
}
