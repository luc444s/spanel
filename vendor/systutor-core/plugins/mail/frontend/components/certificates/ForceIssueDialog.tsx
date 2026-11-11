import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";

import { useT } from "@/i18n/use-i18n";
import type { MailCertificate } from "../../api";

export function ForceIssueDialog({
  target,
  pending,
  disabled,
  onCancel,
  onConfirm,
}: {
  target: MailCertificate | null;
  pending: boolean;
  disabled: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useT();

  return (
    <Dialog
      open={Boolean(target)}
      title={t("Force reissue")}
      description={target?.hostname}
      onClose={onCancel}
      zIndexClassName="z-[1250]"
      maxWidthClassName="max-w-md"
    >
      <div className="space-y-4 text-sm text-foreground">
        <Alert title={t("Rate limit warning")}>
          {t(
            "Let’s Encrypt limits duplicate certificates per domain. Forcing an unnecessary issuance can block new requests for a week."
          )}
        </Alert>
        <p>
          {t("Certbot will be asked to replace the certificate for")}{" "}
          <span className="break-all font-mono text-xs">{target?.hostname}</span>
          {". "}
          {t(
            "Mail keeps working with the current certificate until the new one is validated and installed."
          )}
        </p>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onCancel} disabled={pending}>
            {t("Cancel")}
          </Button>
          <Button type="button" onClick={onConfirm} disabled={pending || disabled}>
            {pending ? t("Requesting...") : t("Force issuance")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
