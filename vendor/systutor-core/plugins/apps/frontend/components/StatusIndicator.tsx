import { cn } from "@systutor/shell/ui/cn";

const POSITIVE_STATUSES = new Set([
  "active", "connected", "healthy", "ready", "running", "succeeded", "up",
]);
const NEGATIVE_STATUSES = new Set([
  "blocked", "dead", "deleted", "down", "error", "exited", "failed", "missing",
  "stop", "stopped", "unhealthy",
]);
const TRANSITIONAL_STATUSES = new Set([
  "building", "created", "degraded", "deploying", "paused", "pending", "queued",
  "restarting", "starting", "stopping",
]);

export function statusDotTone(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (POSITIVE_STATUSES.has(normalized)) return "text-emerald-500";
  if (NEGATIVE_STATUSES.has(normalized)) return "text-red-500";
  if (TRANSITIONAL_STATUSES.has(normalized)) return "text-amber-500";
  return "text-zinc-400";
}

export function StatusIndicator({ status, className }: { status: string; className?: string }) {
  return <span className={cn("inline-flex items-center gap-1.5", className)}>
    <svg aria-hidden="true" viewBox="0 0 8 8" className={cn("h-2 w-2 shrink-0 fill-current", statusDotTone(status))}>
      <circle cx="4" cy="4" r="4" />
    </svg>
    <span>{status}</span>
  </span>;
}
