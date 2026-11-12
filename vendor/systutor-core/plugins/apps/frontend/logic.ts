import { t } from "@/i18n";

import type {
  Application, EnvironmentEntry, Finding, GithubStatus, Operation,
} from "./types";

export const GITHUB_CARD_STATES = [
  "loading", "unconfigured", "registering", "pending_installation",
  "syncing", "connected", "suspended", "error",
] as const;

export type GithubCardState = (typeof GITHUB_CARD_STATES)[number];

const GITHUB_ORGANIZATION = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
const ENVIRONMENT_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
const MANAGED_ENVIRONMENT_NAMES = new Set([
  "APP_DB_NAME", "APP_DB_USER", "APP_DB_PASSWORD",
]);

export const TERMINAL_STATUSES = new Set(["succeeded", "failed", "interrupted"]);
export const isTerminalOperation = (operation?: Pick<Operation, "status"> | null) =>
  Boolean(operation && TERMINAL_STATUSES.has(operation.status));

export function parseDotenv(text: string): EnvironmentEntry[] {
  const entries: EnvironmentEntry[] = [];
  const names = new Set<string>();
  for (const [index, raw] of text.split(/\r?\n/).entries()) {
    const trimmed = raw.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const line = trimmed.startsWith("export ") ? trimmed.slice(7).trimStart() : raw;
    const separator = line.indexOf("=");
    const name = separator < 0 ? "" : line.slice(0, separator).trim();
    if (!ENVIRONMENT_NAME.test(name)) {
      throw new Error(t("Invalid .env variable on line {line}", { line: index + 1 }));
    }
    if (names.has(name)) {
      throw new Error(t("Duplicate .env variable: {name}", { name }));
    }
    if (MANAGED_ENVIRONMENT_NAMES.has(name)) {
      throw new Error(t("Apps manages {name} automatically", { name }));
    }
    const value = line.slice(separator + 1);
    if (value.includes("\0") || value.length > 8192) {
      throw new Error(t("Invalid .env value for {name}", { name }));
    }
    names.add(name);
    entries.push({ name, value });
  }
  if (entries.length > 100) throw new Error(t("The deployment allows up to 100 variables"));
  return entries;
}

const GITHUB_CARD_LABELS: Record<GithubCardState, string> = {
  loading: "Checking GitHub status…",
  unconfigured: "Not connected",
  registering: "Registering GitHub App…",
  pending_installation: "App registered; install it and choose repositories",
  syncing: "Syncing authorized repositories…",
  connected: "GitHub connected",
  suspended: "Installation suspended",
  error: "Could not read GitHub status",
};

const GITHUB_ERROR_MESSAGE = "Could not read GitHub status";

export function githubCardContent(state: GithubCardState) {
  if (state === "error") {
    return { label: null, alert: t(GITHUB_ERROR_MESSAGE) };
  }
  return { label: t(GITHUB_CARD_LABELS[state]), alert: null };
}

export function githubCardState(input: {
  loading: boolean;
  error: boolean;
  registering: boolean;
  status?: GithubStatus | null;
}): GithubCardState {
  if (input.loading) return "loading";
  if (input.error) return "error";
  if (input.registering) return "registering";
  const state = input.status?.state;
  if (state === "syncing") return "syncing";
  if (state === "pending_installation") return "pending_installation";
  if (state === "connected") return "connected";
  if (state === "suspended") return "suspended";
  if (state === "unconfigured") return "unconfigured";
  return "error";
}

export function validateGithubManifestUrl(value: string, organization: string | null) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.hostname !== "github.com" || url.port) {
      throw new Error("invalid");
    }
    if (url.username || url.password || url.search || url.hash) throw new Error("invalid");
    const path = url.pathname.replace(/\/+$/, "");
    if (!organization) {
      if (path !== "/settings/apps/new") throw new Error("invalid");
    } else {
      if (!GITHUB_ORGANIZATION.test(organization)) throw new Error("invalid");
      if (path !== `/organizations/${organization}/settings/apps/new`) throw new Error("invalid");
    }
    return url.href;
  } catch {
    throw new Error(t("GitHub returned an invalid manifest URL"));
  }
}

export function validateGithubInstallUrl(value: string) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.hostname !== "github.com" || url.port) {
      throw new Error("invalid");
    }
    if (url.username || url.password || url.search || url.hash) throw new Error("invalid");
    if (!/^\/apps\/[A-Za-z0-9-_]+\/installations\/new$/.test(url.pathname)) {
      throw new Error("invalid");
    }
    return url.href;
  } catch {
    throw new Error(t("GitHub returned an invalid installation URL"));
  }
}

export function validateGithubOrganization(value: string) {
  const slug = value.trim();
  if (!GITHUB_ORGANIZATION.test(slug)) {
    throw new Error(t("The GitHub organization slug is not valid"));
  }
  return slug;
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

export function canDeploy(permissions: string[]) {
  return permissions.includes("apps.deployments.create");
}

export function canPlatformMutate(permissions: string[], permission: string) {
  return permissions.includes(permission) && permissions.includes("core.platform.admin");
}

export function isOwnedByTenant(
  resourceTenantId: string | null | undefined,
  currentTenantId: string | null | undefined,
) {
  return !resourceTenantId || resourceTenantId === currentTenantId;
}
