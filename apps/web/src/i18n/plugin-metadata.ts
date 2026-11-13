/**
 * Plugin metadata comes from the database, which may still hold Spanish values
 * from an older install. These maps keep the panel English-first without a
 * migration; unknown plugins fall back to the server value.
 */
const PLUGIN_NAMES: Record<string, string> = {
  apps: "Applications",
  domains: "Domains and DNS",
  filemanager: "File Manager",
  hosting: "WordPress Hosting",
  mail: "Mail Administration",
  tenant: "Tenant Management",
};

const PLUGIN_DESCRIPTIONS: Record<string, string> = {
  apps: "Tenant-aware asynchronous Compose application deployments",
  domains: "Web domains: Hosting primaries in read-only mode and extra proxy domains",
  filemanager: "File explorer for authorized targets",
  hosting: "WordPress sites, users, and one-password reset for the current tenant",
  mail: "Mail account management and TLS certificate inventory via Docker Mailserver",
  tenant: "Tenant CRUD and user assignment for superadmin",
};

export function pluginName(pluginId: string, fallback: string): string {
  return PLUGIN_NAMES[pluginId] ?? fallback;
}

export function pluginDescription(pluginId: string, fallback: string | null): string {
  return PLUGIN_DESCRIPTIONS[pluginId] ?? fallback ?? "";
}
