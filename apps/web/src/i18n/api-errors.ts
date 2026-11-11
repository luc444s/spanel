import { setApiErrorLocalizer } from "@systutor/shell/api/client";

import { t } from "./index";

/**
 * Stable error codes from the kernel envelope `{"error": {"code", "message"}}`.
 * Codes not listed here fall back to the server message, so a new backend error
 * never breaks the UI.
 */
export const ERROR_MESSAGES: Record<string, string> = {
  app_error: "The operation could not be completed",
  authentication_error: "Invalid credentials",
  authorization_error: "You do not have permission to perform this action",
  not_found: "The requested resource was not found",
  seed_orphan_admin: "The seed administrator is not assigned to a branch",
  hosting_site_unavailable: "The site is not available",
  hosting_site_not_managed: "The site is not managed by SPanel",
  hosting_site_conflict: "The site conflicts with an existing site",
  hosting_database_unavailable: "The site database is not available",
  hosting_backup_runtime_unavailable: "The backup runtime is not available",
  hosting_backup_storage_unavailable: "The backup storage is not available",
  hosting_backup_too_large: "The backup exceeds the allowed size",
  hosting_backup_invalid: "The backup is not valid for this site",
  hosting_backup_in_progress: "A backup operation is already running for this site",
  hosting_backup_import_invalid: "Select a valid .zip file",
  hosting_backup_confirm_mismatch: "The confirmation does not match the container",
  hosting_phpmyadmin_unavailable: "phpMyAdmin is not available for this site",
  hosting_phpmyadmin_login_failed: "Could not sign in to phpMyAdmin",
  hosting_phpmyadmin_session_expired: "The phpMyAdmin session expired; open phpMyAdmin again",
  hosting_provision_failed: "The site could not be provisioned",
  hosting_profile_invalid: "The static profile is not valid",
  dms_error: "The mail server rejected the operation",
  invalid_email: "The email address is not valid",
  tenant_no_domain: "The tenant has no domain configured",
  service_unavailable: "The service is not available",
  "apps.state_unavailable": "The application state is not available",
  "apps.source_reconnect_required": "Reconnect the application source to continue",
  "apps.operation_active": "Another operation is already running for this application",
};

setApiErrorLocalizer((code, message) => (code ? t(ERROR_MESSAGES[code] ?? message) : t(message)));
