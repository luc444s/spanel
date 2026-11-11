import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { toast } from "@systutor/shell/ui/toast";
import { useState } from "react";

import { t } from "@/i18n";
import { useT } from "@/i18n/use-i18n";

import { createPhpMyAdminSession, type HostingSiteDatabase } from "../api";

type DatabaseDialogProps = {
  access: HostingSiteDatabase | null;
  onClose: () => void;
};

async function copy(label: string, value: string) {
  try {
    await navigator.clipboard.writeText(value);
    toast.success(t("{label} copied", { label }));
  } catch {
    toast.error(t("Could not copy to clipboard"));
  }
}

/**
 * The site's own database credential. It is the user WordPress already uses,
 * limited to this site's database; the panel never creates a second one. The
 * password only lives in this component's props and disappears on close.
 */
export function DatabaseDialog({ access, onClose }: DatabaseDialogProps) {
  const t = useT();
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function close() {
    setError(null);
    onClose();
  }

  async function openPhpMyAdmin() {
    if (!access || opening) return;
    // Opening before awaiting the Bearer-authenticated POST avoids popup blockers.
    const tab = window.open("about:blank", "_blank");
    if (!tab) {
      setError(t("Allow pop-ups to open phpMyAdmin."));
      return;
    }
    tab.opener = null;
    setError(null);
    setOpening(true);
    try {
      const session = await createPhpMyAdminSession(access.site_id);
      if (!tab.closed) tab.location.replace(new URL(session.url, window.location.origin).href);
    } catch (reason) {
      tab.close();
      setError(reason instanceof Error ? reason.message : t("Could not open phpMyAdmin."));
    } finally {
      setOpening(false);
    }
  }

  return (
    <Dialog
      open={Boolean(access)}
      title={t("Database for {site}", { site: access?.container_name ?? "" })}
      description={t("Site-specific credential with access only to its database.")}
      onClose={close}
      zIndexClassName="z-[1100]"
      maxWidthClassName="max-w-xl"
      actions={
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" onClick={close}>
            {t("Close")}
          </Button>
          <Button onClick={openPhpMyAdmin} disabled={opening}>
            {opening ? t("Connecting...") : t("Open phpMyAdmin")}
          </Button>
        </div>
      }
    >
      {access ? (
        <div className="space-y-4">
          {error ? <Alert title={t("Could not open phpMyAdmin")}>{error}</Alert> : null}
          <dl className="grid gap-3 text-sm">
            <div className="grid grid-cols-[8rem_1fr] items-center gap-2">
              <dt className="text-muted-foreground">{t("Server")}</dt>
              <dd className="flex items-center gap-2">
                <span className="break-all font-mono text-xs text-foreground">{access.host}</span>
                <Button variant="secondary" onClick={() => copy(t("Server"), access.host)}>
                  {t("Copy")}
                </Button>
              </dd>
            </div>
            <div className="grid grid-cols-[8rem_1fr] items-center gap-2">
              <dt className="text-muted-foreground">{t("Database")}</dt>
              <dd className="flex items-center gap-2">
                <span className="break-all font-mono text-xs text-foreground">
                  {access.database}
                </span>
                <Button variant="secondary" onClick={() => copy(t("Database"), access.database)}>
                  {t("Copy")}
                </Button>
              </dd>
            </div>
            <div className="grid grid-cols-[8rem_1fr] items-center gap-2">
              <dt className="text-muted-foreground">{t("User")}</dt>
              <dd className="flex items-center gap-2">
                <span className="break-all font-mono text-xs text-foreground">{access.user}</span>
                <Button variant="secondary" onClick={() => copy(t("User"), access.user)}>
                  {t("Copy")}
                </Button>
              </dd>
            </div>
            <div className="grid grid-cols-[8rem_1fr] items-center gap-2">
              <dt className="text-muted-foreground">{t("Password")}</dt>
              <dd className="flex items-center gap-2">
                <span className="break-all font-mono text-xs text-foreground">
                  {access.password}
                </span>
                <Button variant="secondary" onClick={() => copy(t("Password"), access.password)}>
                  {t("Copy")}
                </Button>
              </dd>
            </div>
          </dl>
        </div>
      ) : null}
    </Dialog>
  );
}
