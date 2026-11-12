import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@systutor/shell/ui/card";
import { ConfirmDialog } from "@systutor/shell/ui/confirm-dialog";
import { toast } from "@systutor/shell/ui/toast";
import { t } from "@/i18n";

import {
  cloudflareKeys,
  disconnectCloudflare,
  getCloudflareStatus,
  listCloudflareZones,
  startCloudflareOAuth,
} from "./api";
import {
  CLOUDFLARE_POLL_INTERVAL_MS,
  accountLabel,
  canUseCloudflare,
  errorMessageFrom,
  flowDeadline,
  isCloudflareConnected,
  pollDecision,
  scopeNames,
  zoneCount,
} from "./logic";

type PendingFlow = { flowId: string; deadline: number; authorizationUrl: string | null };

type Props = {
  permissions: readonly string[];
  onConnected?: () => void;
};

export function CloudflareConnectionCard({ permissions, onConnected }: Props) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<PendingFlow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const visible = canUseCloudflare(permissions);
  const statusQuery = useQuery({
    queryKey: cloudflareKeys.status,
    queryFn: getCloudflareStatus,
    enabled: visible,
    retry: false,
  });
  const connected = isCloudflareConnected(statusQuery.data ?? null);
  const zonesQuery = useQuery({
    queryKey: cloudflareKeys.zones,
    queryFn: listCloudflareZones,
    enabled: visible && connected,
    retry: false,
  });

  const startMutation = useMutation({
    mutationFn: startCloudflareOAuth,
    onSuccess: (start) => {
      setError(null);
      setPending({
        flowId: start.flow_id,
        deadline: flowDeadline(start.expires_at, Date.now()),
        authorizationUrl: start.authorization_url,
      });
      const popup = window.open(
        "about:blank",
        "cloudflare-oauth",
        "width=560,height=760",
      );
      if (popup) {
        popup.opener = null;
        popup.location.replace(start.authorization_url);
      }
    },
    onError: (failure: unknown) => setError(t(errorMessageFrom(failure))),
  });

  const disconnectMutation = useMutation({
    mutationFn: disconnectCloudflare,
    onSuccess: () => {
      setConfirmDisconnect(false);
      setError(null);
      toast.success(t("Cloudflare disconnected"));
      void queryClient.invalidateQueries({ queryKey: cloudflareKeys.status });
      void queryClient.invalidateQueries({ queryKey: cloudflareKeys.zones });
      void queryClient.invalidateQueries({ queryKey: ["domains", "dns", "zones"] });
    },
    onError: (failure: unknown) => {
      setConfirmDisconnect(false);
      setError(t(errorMessageFrom(failure)));
    },
  });

  useEffect(() => {
    if (!pending) {
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const finish = (message: string | null) => {
      if (!mounted.current || cancelled) {
        return;
      }
      setPending(null);
      if (message) {
        setError(message);
      }
    };

    const poll = async () => {
      try {
        const status = await getCloudflareStatus();
        const decision = pollDecision(status, pending.deadline, Date.now());
        if (decision === "connected") {
          finish(null);
          toast.success(t("Cloudflare connected"));
          void queryClient.invalidateQueries({ queryKey: cloudflareKeys.status });
          void queryClient.invalidateQueries({ queryKey: cloudflareKeys.zones });
          void queryClient.invalidateQueries({ queryKey: ["domains", "dns", "zones"] });
          onConnected?.();
          return;
        }
        if (decision === "expired") {
          finish(t("Cloudflare authorization expired. Try again."));
          return;
        }
        timer = setTimeout(() => void poll(), CLOUDFLARE_POLL_INTERVAL_MS);
      } catch (failure) {
        const status = typeof failure === "object" && failure !== null
          ? (failure as { status?: unknown }).status
          : null;
        // A 404 while the flow is pending only means "not connected yet".
        if (status === 404) {
          if (Date.now() >= pending.deadline) {
            finish(t("Cloudflare authorization expired. Try again."));
            return;
          }
          timer = setTimeout(() => void poll(), CLOUDFLARE_POLL_INTERVAL_MS);
          return;
        }
        finish(t(errorMessageFrom(failure)));
      }
    };

    timer = setTimeout(() => void poll(), CLOUDFLARE_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, [pending, onConnected, queryClient]);

  if (!visible) {
    return null;
  }

  const status = statusQuery.data ?? null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("Cloudflare")}</CardTitle>
        <CardDescription>
          {connected
            ? t("Cloudflare is connected for this tenant.")
            : t("Connect your Cloudflare account to discover its zones and DNS records.")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {statusQuery.isError && (
          <Alert title={t("Could not read the Cloudflare connection")}>
            {t(errorMessageFrom(statusQuery.error))}
          </Alert>
        )}

        {error && <Alert title={t("Cloudflare error")}>{error}</Alert>}

        {connected ? (
          <div className="space-y-2 text-sm">
            <p>
              <span className="text-muted-foreground">{t("Account")}: </span>
              {accountLabel(status)}
            </p>
            {scopeNames(status) && (
              <p>
                <span className="text-muted-foreground">{t("Scopes")}: </span>
                {scopeNames(status)}
              </p>
            )}
            <p>
              <span className="text-muted-foreground">{t("Zones discovered")}: </span>
              {zoneCount(zonesQuery.data?.items)}
            </p>
            <div className="flex flex-wrap gap-2 pt-1">
              <Button
                size="sm"
                variant="secondary"
                disabled={startMutation.isPending || Boolean(pending)}
                onClick={() => startMutation.mutate()}
              >
                {t("Reconnect Cloudflare")}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={disconnectMutation.isPending}
                onClick={() => setConfirmDisconnect(true)}
              >
                {t("Disconnect Cloudflare")}
              </Button>
            </div>
          </div>
        ) : pending ? (
          <div className="space-y-2 text-sm text-muted-foreground">
            <p>{t("Waiting for Cloudflare authorization...")}</p>
            {pending.authorizationUrl && (
              <p>
                {t("Did the browser block the popup?")}{" "}
                <a
                  className="text-primary underline"
                  href={pending.authorizationUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  {t("Open the authorization page")}
                </a>
              </p>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              {t("No Cloudflare account is connected for this tenant yet.")}
            </p>
            <Button
              size="sm"
              disabled={startMutation.isPending}
              onClick={() => startMutation.mutate()}
            >
              {t("Connect Cloudflare")}
            </Button>
          </div>
        )}
      </CardContent>

      <ConfirmDialog
        open={confirmDisconnect}
        onClose={() => setConfirmDisconnect(false)}
        onConfirm={() => disconnectMutation.mutate()}
        title={t("Disconnect Cloudflare?")}
        description={t(
          "Cloudflare will stop showing its zones for this tenant. You can connect it again at any time.",
        )}
        confirmLabel={t("Disconnect")}
        cancelLabel={t("Cancel")}
        destructive
        loading={disconnectMutation.isPending}
      />
    </Card>
  );
}
