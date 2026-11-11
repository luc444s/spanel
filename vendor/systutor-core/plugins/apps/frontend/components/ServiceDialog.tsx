import { useMemo, useState } from "react";

import { Alert } from "@systutor/shell/ui/alert";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Tabs } from "@systutor/shell/ui/tabs";

import { useT } from "@/i18n/use-i18n";

import type { AppAction, Operation, ServiceDetail } from "../types";
import { ServiceDomains } from "./ServiceDomains";
import { ServiceEnvironment } from "./ServiceEnvironment";
import { ServiceLogs } from "./ServiceLogs";
import { ServiceOperations } from "./ServiceOperations";
import { ServiceOverview } from "./ServiceOverview";

type Tab = "overview" | "variables" | "domains" | "logs" | "operations";
type ServiceAction = Exclude<AppAction, "down">;

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
  const t = useT();
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

  const tabs = [
    {
      value: "overview" as const,
      label: t("Overview"),
      content: <ServiceOverview service={service} canManage={canManage}
        pending={pending} updatedAt={updatedAt} onAction={onAction} />,
    },
    {
      value: "variables" as const,
      label: t("Variables"),
      content: <ServiceEnvironment applicationId={applicationId}
        service={service} canReveal={canReveal} />,
    },
    {
      value: "domains" as const,
      label: t("Domains"),
      content: canReadDomains
        ? <ServiceDomains applicationId={applicationId} service={service}
          canManage={canManage && canManageDomains} />
        : <Alert title={t("No permission")}>{t("You need dominios.domains.read to view the routes for this service.")}</Alert>,
    },
    {
      value: "logs" as const,
      label: t("Logs"),
      content: <ServiceLogs operation={lastLogs} canManage={canManage}
        pending={pending} onRequest={() => onAction("logs")} />,
    },
    {
      value: "operations" as const,
      label: t("Operations"),
      content: <ServiceOperations operations={scoped}
        emptyMessage={t("No operations for this service.")} />,
    },
  ];

  return <Dialog open title={`${applicationName} / ${service.name}`}
    description={t("Compose service: {name}", { name: service.name })} onClose={onClose}
    maxWidthClassName="max-w-[900px]" zIndexClassName="z-[1100]">
    <Tabs value={tab} onChange={(value) => setTab(value as Tab)} tabs={tabs} />
  </Dialog>;
}
