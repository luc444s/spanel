export function hasRequiredPermissions(
  userPermissions: string[],
  requiredPermissions: string[] | undefined,
  isSuperadmin = false
) {
  if (isSuperadmin) {
    return true;
  }

  if (!requiredPermissions || requiredPermissions.length === 0) {
    return true;
  }

  return requiredPermissions.every((permission) => userPermissions.includes(permission));
}


export function hasAnyPermission(
  userPermissions: string[],
  requiredPermissions: string[] | undefined,
  isSuperadmin = false
) {
  if (isSuperadmin) {
    return true;
  }

  if (!requiredPermissions || requiredPermissions.length === 0) {
    return true;
  }

  return requiredPermissions.some((permission) => userPermissions.includes(permission));
}
