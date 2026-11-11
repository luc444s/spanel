import { useEffect, useRef } from "react";

import { toast } from "@systutor/shell/ui/toast";

import { useT } from "@/i18n/use-i18n";

import { isTerminalOperation } from "../logic";
import type { Operation } from "../types";
import { useOperationPolling } from "../useOperationPolling";

const PHASES: Record<string, string> = {
  queued: "Queued", cloning: "Cloning", auditing: "Auditing", staging: "Preparing",
  building: "Building", verifying_images: "Verifying images", starting: "Starting",
  verifying_runtime: "Verifying runtime", publishing: "Publishing", complete: "Completed",
  failed: "Failed",
};

export function OperationTracker({ operationId, applicationId, onUpdate }: {
  operationId: string;
  applicationId: string;
  onUpdate: (operation: Operation) => void;
}) {
  const t = useT();
  const query = useOperationPolling(operationId, applicationId);
  const notified = useRef(false);
  useEffect(() => {
    if (!query.data) return;
    onUpdate(query.data);
    if (!isTerminalOperation(query.data) || notified.current) return;
    notified.current = true;
    if (query.data.status === "succeeded") toast.success(t("{kind}: operation completed", { kind: query.data.kind }));
    else toast.error(query.data.public_error || t("The operation failed"));
  }, [onUpdate, query.data]);

  const operation = query.data;
  const phaseLabel = operation?.phase ? PHASES[operation.phase] : undefined;
  return <div className="rounded-md border border-border bg-card px-3 py-2 text-sm">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="font-medium">{operation?.kind ?? t("Operation")}</span>
      <span className="text-muted-foreground">{phaseLabel ? t(phaseLabel) : operation?.phase ?? t(PHASES.queued)}</span>
    </div>
    <code className="text-xs text-muted-foreground">{operationId}</code>
    {query.error ? <p className="text-destructive">{t("Could not query the operation.")}</p> : null}
  </div>;
}
