export function defaultLandingPath(permissions: string[]): string {
  return permissions.includes("domains.domains.read") ? "/app/domains" : "/app/dashboard";
}
