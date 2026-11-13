import type { CoreRole, CoreUser } from "../core-management/api";

export function assignedRoleId(user: CoreUser | undefined, roles: CoreRole[]): string | null {
  if (!user || user.roles.length !== 1) return null;
  return roles.find((role) => role.name === user.roles[0])?.id ?? null;
}

export function roleMemberCount(role: CoreRole, users: CoreUser[]): number {
  return users.filter((user) => user.roles.includes(role.name)).length;
}
