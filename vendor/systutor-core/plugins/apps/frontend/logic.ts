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
      throw new Error(`Variable .env inválida en la línea ${index + 1}`);
    }
    if (names.has(name)) {
      throw new Error(`Variable .env duplicada: ${name}`);
    }
    const value = line.slice(separator + 1);
    if (value.includes("\0") || value.length > 8192) {
      throw new Error(`Valor .env inválido para ${name}`);
    }
    names.add(name);
    entries.push({ name, value });
  }
  if (entries.length > 100) throw new Error("El deployment admite hasta 100 variables");
  return entries;
}

const GITHUB_CARD_LABELS: Record<GithubCardState, string> = {
  loading: "Comprobando estado de GitHub…",
  unconfigured: "Sin conexión",
  registering: "Registrando GitHub App…",
  pending_installation: "App registrada; falta instalar y elegir repositorios",
  syncing: "Sincronizando repositorios autorizados…",
  connected: "GitHub conectado",
  suspended: "Instalación suspendida",
  error: "No se pudo leer el estado de GitHub",
};

const GITHUB_ERROR_MESSAGE = "No se pudo leer el estado de GitHub";

export function githubCardContent(state: GithubCardState) {
  if (state === "error") {
    return { label: null, alert: GITHUB_ERROR_MESSAGE };
  }
  return { label: GITHUB_CARD_LABELS[state], alert: null };
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
    throw new Error("GitHub devolvió una URL de manifest no válida");
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
    throw new Error("GitHub devolvió una URL de instalación no válida");
  }
}

export function validateGithubOrganization(value: string) {
  const slug = value.trim();
  if (!GITHUB_ORGANIZATION.test(slug)) {
    throw new Error("El slug de organización de GitHub no es válido");
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

export function canPlatformMutate(permissions: string[], permission: string) {
  return permissions.includes(permission) && permissions.includes("core.platform.admin");
}
