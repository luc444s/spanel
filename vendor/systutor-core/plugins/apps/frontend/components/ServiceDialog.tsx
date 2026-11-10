import { useMemo, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";

import type { AppAction, Operation, ServiceDetail } from "../types";
import { ServiceDomains } from "./ServiceDomains";
import { ServiceEnvironment } from "./ServiceEnvironment";
import { ServiceLogs } from "./ServiceLogs";
import { ServiceOperations } from "./ServiceOperations";
import { ServiceOverview } from "./ServiceOverview";

type Tab = "overview" | "variables" | "domains" | "logs" | "operations";
type ServiceAction = Exclude<AppAction, "down">;

const TABS: Array<[Tab, string]> = [
  ["overview", "Overview"],
  ["variables", "Variables"],
  ["domains", "Dominios"],
  ["logs", "Logs"],
  ["operations", "Operaciones"],
];

export function ServiceDialog({ applicationId, applicationName, service, operations,
  canManage, canManageDomains, canReadDomains, canReveal, pending, updatedAt,
  onClose, onAction }: {
  applicationId: string;
  applicationName: string;
  service: ServiceDetail;
  operations: Operation[];
  canManage: boolean;
  canManageDomains: boolean;
  canReadDomains: boolean;
  canReveal: boolean;
  pending: boolean;
  updatedAt: number;
  onClose: () => void;
  onAction: (action: ServiceAction) => void;
}) {
  const [tab, setTab] = useState<Tab>("overview");
  const subject = `${applicationId}:${service.name}`;
  const scoped = useMemo(() => operations.filter((operation) =>
    operation.subject_id === subject
    || (operation.subject_id === applicationId && ["refresh", "logs"].includes(operation.kind))
  ).sort((left, right) =>
    String(right.created_at ?? "").localeCompare(String(left.created_at ?? ""))),
  [applicationId, operations, subject]);
  const lastLogs = scoped.find((operation) =>
    operation.kind === "logs" && operation.subject_id === subject && operation.status === "succeeded");

  return <Dialog open title={`${applicationName} / ${service.name}`}
    description={`Servicio Compose: ${service.name}`} onClose={onClose}
    maxWidthClassName="max-w-4xl" zIndexClassName="z-[1100]">
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1 border-b border-border pb-2">
        {TABS.map(([value, label]) => <Button key={value} size="sm"
          variant={tab === value ? "primary" : "secondary"}
          onClick={() => setTab(value)}>{label}</Button>)}
      </div>
      {tab === "overview" ? <ServiceOverview service={service} canManage={canManage}
        pending={pending} updatedAt={updatedAt} onAction={onAction} /> : null}
      {tab === "variables" ? <ServiceEnvironment applicationId={applicationId}
        service={service} canReveal={canReveal} /> : null}
      {tab === "domains" ? (canReadDomains
        ? <ServiceDomains applicationId={applicationId} service={service}
          canManage={canManage && canManageDomains} />
        : <Alert title="Sin permiso">Necesitás dominios.domains.read para consultar las rutas de este servicio.</Alert>) : null}
      {tab === "logs" ? <ServiceLogs operation={lastLogs} canManage={canManage}
        pending={pending} onRequest={() => onAction("logs")} /> : null}
      {tab === "operations" ? <ServiceOperations operations={scoped} /> : null}
    </div>
  </Dialog>;
}
