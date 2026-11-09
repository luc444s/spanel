export type GithubConnection = {
  connected: boolean;
  reconnect_required: boolean;
  github_user_id?: string;
  login?: string;
  avatar_url?: string | null;
  scopes?: string[];
};

export type Application = {
  id: string;
  name: string;
  slug: string;
  repository: string;
  branch: string;
  compose_file: string | null;
  domain: string | null;
  ingress_service: string | null;
  ingress_port: number | null;
  status: string;
  source_connection_user_id: string;
};

export type ApplicationCreate = {
  name: string;
  repository: string;
  branch: string;
  compose_file: string | null;
  domain: string | null;
  ingress_service: string | null;
  ingress_port: number | null;
};

export type OperationSummary = {
  operation_id: string;
  application_id: string;
  kind: string;
  status: string;
  poll_url: string;
};

export type Finding = {
  severity: "error" | "warning" | "info";
  code: string;
  service: string | null;
  path: string;
  line: number;
  message: string;
  remediation: string;
};

export type AuditReport = {
  compose?: string;
  findings?: Finding[];
  blocking?: boolean;
  compose_candidates?: string[];
};

export type Operation = {
  id: string;
  application_id: string;
  kind: string;
  status: string;
  phase: string;
  report: AuditReport | Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  error_code: string | null;
  public_error: string | null;
};

export type Deployment = {
  id: string;
  status: string;
  commit: string | null;
  tree_hash: string | null;
  public_error: string | null;
};

export type EnvironmentEntry = { name: string; value: string };
export type AppAction = "refresh" | "logs" | "start" | "stop" | "restart" | "down";
