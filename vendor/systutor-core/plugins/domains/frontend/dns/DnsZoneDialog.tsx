import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { t } from "@/i18n";
import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { ConfirmDialog } from "@systutor/shell/ui/confirm-dialog";
import { Dialog } from "@systutor/shell/ui/dialog";
import { ApiError } from "@systutor/shell/api/client";
import { createZone, dnsKeys, getZone } from "./api";
import { safeErrorMessage } from "./logic";
import { RecordsTab } from "./RecordsTab";
import { ZoneTab } from "./ZoneTab";

export function DnsZoneDialog({ domain, zoneName, permissions, owned, zoneListError, retryZoneList, onClose }: {
  domain: string | null;
  zoneName: string | null;
  permissions: string[];
  owned: boolean;
  zoneListError: boolean;
  retryZoneList: () => void;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [tab, setTab] = useState<"records" | "zone">("records");
  const [confirmActivation, setConfirmActivation] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [activationError, setActivationError] = useState<string | null>(null);
  const query = useQuery({
    queryKey: dnsKeys.zone(zoneName ?? ""),
    queryFn: () => getZone(zoneName!),
    enabled: Boolean(domain && zoneName),
    retry: false,
  });
  const activation = useMutation({
    mutationFn: () => createZone(domain!),
    onSuccess: () => {
      setConfirmActivation(false);
      setActivationError(null);
      void client.invalidateQueries({ queryKey: dnsKeys.zones });
      void client.invalidateQueries({ queryKey: dnsKeys.zone(domain!) });
    },
    onError: (error) => {
      setConfirmActivation(false);
      if (error instanceof ApiError && error.status === 422) setBlocked(true);
      else setActivationError(safeErrorMessage(error));
    },
  });
  const close = () => {
    setConfirmActivation(false);
    setBlocked(false);
    setActivationError(null);
    onClose();
  };
  const refresh = () => void query.refetch();
  const closeAfterDelete = () => {
    void client.invalidateQueries({ queryKey: dnsKeys.zones });
    close();
  };

  return <>
    <Dialog open={Boolean(domain)} title={domain ?? ""}
      description={zoneName && query.data ? t("zone {zone} · version {version}", { zone: query.data.name, version: query.data.version ?? "—" }) : t("DNS · zone not managed by PanelTutor")}
      onClose={close} maxWidthClassName="max-w-[800px]">
      {zoneListError ? <Alert title={t("Could not load DNS zones")}><Button size="sm" variant="secondary" onClick={retryZoneList}>{t("Retry")}</Button></Alert>
        : blocked ? <Alert title={t("Public nameservers are not configured.")}>{t("A platform administrator must configure ns1 and ns2 before the first zone can be activated.")}</Alert>
        : !zoneName ? <div className="space-y-4">
          <p className="text-sm text-muted-foreground">{t("This domain is not authoritative here. Its records are managed by an external DNS provider, for example Cloudflare.")}</p>
          <p className="text-sm text-muted-foreground">{t("Activating the zone creates SOA, NS and local glue records. It does not change registrar delegation, DNSSEC or Cloudflare.")}</p>
          {activationError && <Alert title={t("Could not activate DNS zone")}>{activationError}</Alert>}
          {owned && permissions.includes("domains.dns.zones") && <div className="flex justify-end"><Button onClick={() => { setActivationError(null); setConfirmActivation(true); }}>{t("Activate DNS zone")}</Button></div>}
        </div> : query.isLoading ? <p>{t("Loading DNS zone...")}</p>
          : query.isError ? <Alert title={t("Could not load DNS zone")}><span>{safeErrorMessage(query.error)}</span><Button size="sm" variant="secondary" onClick={refresh}>{t("Retry")}</Button></Alert>
            : query.data && <div className="space-y-4">
              <div className="flex border-b border-border">
                {(["records", "zone"] as const).map((value) => <button key={value} className={`px-4 py-2 text-sm ${tab === value ? "border-b-2 border-primary" : ""}`} onClick={() => setTab(value)}>{t(value === "records" ? "Records" : "Zone")}</button>)}
              </div>
              {tab === "records" ? <RecordsTab zone={query.data} canWrite={permissions.includes("domains.dns.records")} refresh={refresh} />
                : <ZoneTab zone={query.data} canRecords={permissions.includes("domains.dns.records")} canZones={permissions.includes("domains.dns.zones")} refresh={refresh} onDeleted={closeAfterDelete} />}
            </div>}
    </Dialog>
    <ConfirmDialog open={confirmActivation} title={t("Activate DNS zone {zone}?", { zone: domain ?? "" })}
      description={t("SOA, NS and local glue records will be created.")} confirmLabel={t("Activate DNS zone")} cancelLabel={t("Cancel")}
      loading={activation.isPending} onClose={() => setConfirmActivation(false)} onConfirm={() => { setActivationError(null); activation.mutate(); }} />
  </>;
}
