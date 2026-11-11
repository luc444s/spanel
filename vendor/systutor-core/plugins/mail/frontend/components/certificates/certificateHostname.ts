import { t } from "@/i18n";

/** Validación y normalización pura de hostnames `mail.<dominio>` (A.SPEC 0041). */

const HOSTNAME_PATTERN = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$/;

export const MAIL_PREFIX = "mail.";

/** Minúsculas, sin espacios laterales y sin punto final. */
export function normalizeCertificateHostname(value: string): string {
  return value.trim().toLowerCase().replace(/\.+$/, "");
}

/**
 * Devuelve un mensaje de error si el hostname no tiene la forma
 * `mail.<dominio>` válida, o null si se puede enviar.
 */
export function certificateHostnameError(value: string): string | null {
  const hostname = normalizeCertificateHostname(value);
  if (!hostname) {
    return t("Enter the certificate hostname.");
  }
  if (!HOSTNAME_PATTERN.test(hostname)) {
    return t("Invalid hostname: only letters, numbers, hyphens, and periods.");
  }
  if (!hostname.startsWith(MAIL_PREFIX)) {
    return t("The hostname must have the form mail.<domain>.");
  }
  const domain = hostname.slice(MAIL_PREFIX.length);
  if (!domain) {
    return t("Missing the domain after mail.");
  }
  return null;
}
