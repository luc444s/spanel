import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { t } from "@/i18n";
import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { ApiError } from "@systutor/shell/api/client";
import { applyRrsets, getZone, planRrsets } from "./api";
import { describePlanChanges, safeErrorMessage, SUPPORTED_TYPES, validateRrset } from "./logic";
import type { Plan, RrsetDraft } from "./types";

export function RrsetEditor({ zone, initial, onClose, onApplied }: {
  zone: string;
  initial: RrsetDraft | null | undefined;
  onClose: () => void;
  onApplied: () => void;
}) {
  const empty: RrsetDraft = { name: "@", type: "A", ttl: 300, values: [""] };
  const [draft, setDraft] = useState<RrsetDraft>(initial ?? empty);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [conflict, setConflict] = useState<{ seen: string; current: string } | null>(null);
  const [unavailable, setUnavailable] = useState<"plan" | "apply" | null>(null);

  useEffect(() => {
    setDraft(initial ?? empty);
    setPlan(null);
    setErrors({});
    setConflict(null);
    setUnavailable(null);
  }, [initial, zone]);

  const planMutation = useMutation({
    mutationFn: () => planRrsets(zone, [draft]),
    onSuccess: (result) => { setPlan(result); setUnavailable(null); },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 422) setErrors({ values: safeErrorMessage(error) });
      else if (error instanceof ApiError && [503, 504].includes(error.status)) setUnavailable("plan");
      else setErrors({ form: safeErrorMessage(error) });
    },
  });
  const applyMutation = useMutation({
    mutationFn: () => applyRrsets(zone, [draft], plan!.version),
    onSuccess: onApplied,
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        const seen = plan?.version ?? "—";
        setPlan(null);
        void getZone(zone).then((current) => setConflict({
          seen, current: current.version ?? "—",
        })).catch(() => setConflict({ seen, current: "—" }));
      }
      else if (error instanceof ApiError && error.status === 422) setErrors({ values: safeErrorMessage(error) });
      else if (error instanceof ApiError && [503, 504].includes(error.status)) setUnavailable("apply");
      else setErrors({ form: safeErrorMessage(error) });
    },
  });

  function update(next: RrsetDraft) {
    setDraft(next);
    setPlan(null);
    setErrors({});
    setUnavailable(null);
  }

  function requestPlan() {
    const nextErrors = validateRrset(draft);
    setErrors(nextErrors);
    if (!Object.keys(nextErrors).length) planMutation.mutate();
  }

  return <Dialog
    open={initial !== undefined}
    title={t(initial ? "Edit RRset — {name}" : "New RRset", { name: initial?.name ?? "" })}
    onClose={onClose}
    maxWidthClassName="max-w-2xl"
  >
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="space-y-1 text-sm"><span>{t("Name")}</span>
          <Input value={draft.name} onChange={(e) => update({ ...draft, name: e.target.value })} />
          {errors.name && <small className="text-destructive">{t(errors.name)}</small>}
        </label>
        <label className="space-y-1 text-sm"><span>{t("Type")}</span>
          <select className="w-full rounded-md border border-input bg-background px-2 py-2" value={draft.type}
            onChange={(e) => update({ ...draft, type: e.target.value })}>
            {SUPPORTED_TYPES.map((type) => <option key={type}>{type}</option>)}
          </select>
        </label>
        <label className="space-y-1 text-sm"><span>{t("TTL")}</span>
          <Input type="number" value={draft.ttl} onChange={(e) => update({ ...draft, ttl: Number(e.target.value) })} />
          {errors.ttl && <small className="text-destructive">{t(errors.ttl)}</small>}
        </label>
      </div>
      <div className="space-y-2">
        <span className="text-sm">{t("Values")}</span>
        {draft.values.map((value, index) => <div className="flex gap-2" key={index}>
          <Input value={value} aria-label={t("Value {number}", { number: index + 1 })}
            onChange={(e) => update({ ...draft, values: draft.values.map((item, at) => at === index ? e.target.value : item) })} />
          {draft.values.length > 1 && <Button variant="secondary" onClick={() => update({ ...draft, values: draft.values.filter((_, at) => at !== index) })}>{t("Remove")}</Button>}
        </div>)}
        <Button variant="secondary" size="sm" onClick={() => update({ ...draft, values: [...draft.values, ""] })}>{t("+ Add value")}</Button>
        {errors.values && <p className="text-xs text-destructive">{t(errors.values)}</p>}
      </div>
      {plan && <div className="rounded border border-border p-3 text-sm">
        <strong>{t("Plan preview")}</strong>
        <ul className="mt-2 space-y-1 font-mono">{describePlanChanges(plan.changes).map((line) => <li key={line}>{line}</li>)}</ul>
      </div>}
      {conflict && <Alert title={t("Zone changed since you opened it. Nothing was written.")}><div className="space-y-2"><p>{t("Version you saw: {version}", { version: conflict.seen })}</p><p>{t("Current version: {version}", { version: conflict.current })}</p><p>{t("Reload the zone and plan the change again.")}</p><Button size="sm" variant="secondary" onClick={onApplied}>{t("Reload zone")}</Button></div></Alert>}
      {unavailable && <Alert title={t("PowerDNS is unavailable.")}><div className="flex items-center justify-between gap-2"><span>{t("No change was applied.")}</span><Button size="sm" variant="secondary" onClick={() => unavailable === "plan" ? requestPlan() : applyMutation.mutate()}>{t("Retry")}</Button></div></Alert>}
      {errors.form && <Alert title={t("Could not change RRset")}>{t(errors.form)}</Alert>}
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button>
        <Button onClick={requestPlan} disabled={planMutation.isPending}>{t("Plan")}</Button>
        <Button onClick={() => applyMutation.mutate()} disabled={!plan || applyMutation.isPending}>{t("Apply changes")}</Button>
      </div>
    </div>
  </Dialog>;
}
