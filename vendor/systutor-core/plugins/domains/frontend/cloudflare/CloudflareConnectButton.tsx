import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Button } from "@systutor/shell/ui/button";
import { ConfirmDialog } from "@systutor/shell/ui/confirm-dialog";
import { toast } from "@systutor/shell/ui/toast";
import { t } from "@/i18n";

import {
  cloudflareKeys,
  disconnectCloudflare,
  getCloudflareClient,
  getCloudflareStatus,
  startCloudflareOAuth,
} from "./api";
import {
  CLOUDFLARE_POLL_INTERVAL_MS,
  canManageCloudflareClient,
  canUseCloudflare,
  errorMessageFrom,
  flowDeadline,
  isCloudflareConnected,
  needsClientRegistration,
  pollDecision,
} from "./logic";
import { CloudflareRegisterDialog } from "./CloudflareRegisterDialog";

type PendingFlow = { deadline: number };

type Props = {
  permissions: readonly string[];
  onConnected?: () => void;
};

/**
 * One header button, next to Nameservers: connect when absent, disconnect when
 * already authorized. No extra card or inline panel.
 */
export function CloudflareConnectButton({ permissions, onConnected }: Props) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<PendingFlow | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [registerOpen, setRegisterOpen] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const visible = canUseCloudflare(permissions);
  const canRegister = canManageCloudflareClient(permissions);
  const clientQuery = useQuery({
    queryKey: cloudflareKeys.client,
    queryFn: getCloudflareClient,
    enabled: visible && canRegister,
    retry: false,
  });
  const statusQuery = useQuery({
    queryKey: cloudflareKeys.status,
    queryFn: getCloudflareStatus,
    enabled: visible,
    retry: false,
  });
  const connected = isCloudflareConnected(statusQuery.data ?? null);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: cloudflareKeys.status });
    void queryClient.invalidateQueries({ queryKey: cloudflareKeys.zones });
    void queryClient.invalidateQueries({ queryKey: ["domains", "dns", "zones"] });
  };

  const startMutation = useMutation({
    mutationFn: startCloudflareOAuth,
    onSuccess: (start) => {
      setPending({ deadline: flowDeadline(start.expires_at, Date.now()) });
      const popup = window.open("about:blank", "cloudflare-oauth", "width=560,height=760");
      if (popup) {
        popup.opener = null;
        popup.location.replace(start.authorization_url);
      } else {
        toast.info(t("Allow popups or open the authorization page from the browser prompt."));
        window.open(start.authorization_url, "_blank", "noreferrer");
      }
    },
    onError: (failure: unknown) => toast.error(t(errorMessageFrom(failure))),
  });

  const disconnectMutation = useMutation({
    mutationFn: disconnectCloudflare,
    onSuccess: () => {
      setConfirmDisconnect(false);
      toast.success(t("Cloudflare disconnected"));
      refresh();
    },
    onError: (failure: unknown) => {
      setConfirmDisconnect(false);
      toast.error(t(errorMessageFrom(failure)));
    },
  });

  useEffect(() => {
    if (!pending) {
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const stop = () => {
      if (mounted.current && !cancelled) {
        setPending(null);
      }
    };

    const poll = async () => {
      try {
        const status = await getCloudflareStatus();
        const decision = pollDecision(status, pending.deadline, Date.now());
        if (decision === "connected") {
          stop();
          toast.success(t("Cloudflare connected"));
          refresh();
          onConnected?.();
          return;
        }
        if (decision === "expired") {
          stop();
          toast.error(t("Cloudflare authorization expired. Try again."));
          return;
        }
        timer = setTimeout(() => void poll(), CLOUDFLARE_POLL_INTERVAL_MS);
      } catch (failure) {
        const status = typeof failure === "object" && failure !== null
          ? (failure as { status?: unknown }).status
          : null;
        if (status === 404) {
          if (Date.now() >= pending.deadline) {
            stop();
            toast.error(t("Cloudflare authorization expired. Try again."));
            return;
          }
          timer = setTimeout(() => void poll(), CLOUDFLARE_POLL_INTERVAL_MS);
          return;
        }
        stop();
        toast.error(t(errorMessageFrom(failure)));
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

  const busy = startMutation.isPending || Boolean(pending);

  return (
    <>
      <Button
        size="sm"
        variant="secondary"
        disabled={busy || disconnectMutation.isPending}
        onClick={() => {
          if (connected) {
            setConfirmDisconnect(true);
          } else if (canRegister && needsClientRegistration(clientQuery.data)) {
            setRegisterOpen(true);
          } else {
            startMutation.mutate();
          }
        }}
      >
        {busy
          ? t("Authorizing Cloudflare...")
          : connected
            ? t("Disconnect Cloudflare")
            : t("Connect Cloudflare")}
      </Button>

      <CloudflareRegisterDialog
        open={registerOpen}
        redirectUri={clientQuery.data?.redirect_uri ?? null}
        onClose={() => setRegisterOpen(false)}
        onRegistered={() => {
          void queryClient.invalidateQueries({ queryKey: cloudflareKeys.client });
        }}
      />

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
    </>
  );
}
