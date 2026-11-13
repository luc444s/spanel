import { useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input, noAutofillProps } from "@systutor/shell/ui/input";
import { toast } from "@systutor/shell/ui/toast";
import { t } from "@/i18n";

import { registerCloudflareClient } from "./api";
import { errorMessageFrom } from "./logic";

type Props = {
  open: boolean;
  redirectUri: string | null;
  onClose: () => void;
  onRegistered: () => void;
};

export function CloudflareRegisterDialog({ open, redirectUri, onClose, onRegistered }: Props) {
  const [token, setToken] = useState("");
  const [accountId, setAccountId] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [showAccount, setShowAccount] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function reset() {
    setToken("");
    setAccountId("");
    setShowToken(false);
    setShowAccount(false);
    setError(null);
  }

  function close() {
    reset();
    onClose();
  }

  async function submit() {
    const value = token.trim();
    if (!value) {
      setError(t("Paste the Cloudflare API token first."));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await registerCloudflareClient({
        bootstrap_token: value,
        ...(showAccount && accountId.trim() ? { account_id: accountId.trim() } : {}),
      });
      reset();
      toast.success(t("Cloudflare OAuth client registered"));
      onRegistered();
      onClose();
    } catch (failure) {
      const status = typeof failure === "object" && failure !== null
        ? (failure as { status?: unknown }).status
        : null;
      if (
        typeof failure === "object"
        && failure !== null
        && (failure as { code?: unknown }).code
          === "domains.dns.cloudflare.account_id_required"
      ) {
        setShowAccount(true);
      }
      setError(status === 403
        ? t("The API token cannot manage OAuth clients. Confirm Account → OAuth Clients → Edit and the correct Account ID.")
        : t(errorMessageFrom(failure)));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      open={open}
      title={t("Register Cloudflare")}
      description={t("Create or repair the private OAuth client with your Cloudflare API token.")}
      onClose={close}
    >
      <div className="space-y-3">
        <label className="block space-y-1 text-sm text-foreground">
          <span>{t("API token")}</span>
          <div className="flex gap-2">
            <Input
              type="text"
              name="cloudflare-api-token"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="cf-..."
              spellCheck={false}
              autoCapitalize="none"
              autoFocus
              {...noAutofillProps}
              data-bwignore="true"
              style={showToken ? undefined : { WebkitTextSecurity: "disc" }}
            />
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => setShowToken((value) => !value)}
            >
              {showToken ? t("Hide") : t("Show")}
            </Button>
          </div>
        </label>

        <p className="text-xs text-muted-foreground">
          {t("Needs Account → OAuth Clients → Edit. It is used once and never stored.")}
          {" "}
          {t("If the token cannot list accounts, enter the Account ID below.")}
        </p>

        {showAccount ? (
          <label className="block space-y-1 text-sm text-foreground">
            <span>{t("Account ID")}</span>
            <Input
              value={accountId}
              onChange={(event) => setAccountId(event.target.value)}
              placeholder="0123456789abcdef0123456789abcdef"
              autoComplete="off"
              spellCheck={false}
            />
            <span className="text-xs text-muted-foreground">
              {t("Only needed when the token cannot list a single account.")}
            </span>
          </label>
        ) : (
          <button
            type="button"
            className="text-xs text-primary underline"
            onClick={() => setShowAccount(true)}
          >
            {t("The token cannot detect my account")}
          </button>
        )}

        {redirectUri && (
          <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs">
            <span className="text-muted-foreground">{t("Redirect URI")}: </span>
            <code className="break-all">{redirectUri}</code>
          </div>
        )}

        <p className="text-xs text-muted-foreground">
          <a
            className="text-primary underline"
            href="https://dash.cloudflare.com/?to=/:account/oauth-clients"
            target="_blank"
            rel="noreferrer"
          >
            {t("Open Cloudflare OAuth clients")}
          </a>
        </p>

        {error && <Alert title={t("Could not register Cloudflare")}>{error}</Alert>}

        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="secondary" size="sm" onClick={close} disabled={saving}>
            {t("Cancel")}
          </Button>
          <Button type="button" size="sm" onClick={() => void submit()} disabled={saving}>
            {saving ? t("Registering...") : t("Register")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
