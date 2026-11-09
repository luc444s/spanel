import type { Application, Finding, Operation } from "./types";

export const TERMINAL_STATUSES = new Set(["succeeded", "failed", "interrupted"]);
export const isTerminalOperation = (operation?: Pick<Operation, "status"> | null) =>
  Boolean(operation && TERMINAL_STATUSES.has(operation.status));

export function validateGithubAuthorizationUrl(value: string) {
  const url = new URL(value, window.location.origin);
  if (url.protocol !== "https:" || url.hostname !== "github.com") {
    throw new Error("GitHub devolvió una URL de autorización no válida");
  }
  return url.href;
}

export function groupedFindings(findings: Finding[]) {
  return {
    error: findings.filter((item) => item.severity === "error"),
    warning: findings.filter((item) => item.severity === "warning"),
    info: findings.filter((item) => item.severity === "info"),
  };
}

export const hasBlockingFindings = (findings: Finding[]) =>
  findings.some((item) => item.severity === "error");

export function actionAllowed(application: Application, action: string, busy: boolean) {
  if (busy || !["running", "stopped", "degraded"].includes(application.status)) return false;
  if (action === "start") return application.status !== "running";
  if (action === "stop") return application.status === "running";
  return true;
}

export function canPlatformMutate(permissions: string[], permission: string) {
  return permissions.includes(permission) && permissions.includes("core.platform.admin");
}
