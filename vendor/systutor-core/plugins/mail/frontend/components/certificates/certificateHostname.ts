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
    return "Ingresá el hostname del certificado.";
  }
  if (!HOSTNAME_PATTERN.test(hostname)) {
    return "Hostname inválido: solo letras, números, guiones y puntos.";
  }
  if (!hostname.startsWith(MAIL_PREFIX)) {
    return "El hostname debe tener la forma mail.<dominio>.";
  }
  const domain = hostname.slice(MAIL_PREFIX.length);
  if (!domain) {
    return "Falta el dominio después de mail.";
  }
  return null;
}
