export type CloudflareStartResponse = {
  tenant_id: string;
  flow_id: string;
  authorization_url: string;
  redirect_uri: string;
  scopes: string[];
  expires_at: string | null;
};

export type CloudflareStatus = {
  connected: boolean;
  tenant_id: string;
  account_id: string | null;
  account_name: string | null;
  scopes: string[];
  expires_at: string | null;
  status: string;
  updated_at: string | null;
};

export type CloudflareZone = {
  id: string;
  name: string;
  status: string;
  account_id: string;
  account_name: string;
  name_servers: string[];
  created_on: string | null;
  modified_on: string | null;
  record_count: number;
};

export type CloudflareZoneList = { items: CloudflareZone[] };

export type CloudflareDisconnectResponse = {
  tenant_id: string;
  applied: boolean;
  disconnected: boolean;
  already_disconnected: boolean;
  revoked: boolean;
  account_id: string | null;
  account_name: string | null;
  scopes: string[];
  pending_flows: number;
  cancelled_flows: number;
};
