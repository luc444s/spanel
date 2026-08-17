import { createContext, useContext, type ReactNode } from 'react'
import type { UserProfile } from '@systutor/shell'

export type PermissionMeta = {
  requiredAnyPermissions?: string[]
  requiredAllPermissions?: string[]
}

type AuthzContextValue = {
  user: UserProfile | null
}

const AuthzContext = createContext<AuthzContextValue>({ user: null })

export function AuthzProvider({ user, children }: { user: UserProfile | null, children: ReactNode }) {
  return <AuthzContext.Provider value={{ user }}>{children}</AuthzContext.Provider>
}

export function hasAnyPermission(permissions: string[], required?: string[]) {
  if (!required || required.length === 0) return true
  return required.some((permission) => permissions.includes(permission))
}

export function hasAllPermissions(permissions: string[], required?: string[]) {
  if (!required || required.length === 0) return true
  return required.every((permission) => permissions.includes(permission))
}

export function hasPermissionMeta(permissions: string[], meta: PermissionMeta) {
  return (
    hasAnyPermission(permissions, meta.requiredAnyPermissions) &&
    hasAllPermissions(permissions, meta.requiredAllPermissions)
  )
}

export function useAuthz() {
  const { user } = useContext(AuthzContext)
  const permissions = user?.permissions ?? []

  return {
    user,
    permissions,
    hasAnyPermission: (required?: string[]) => hasAnyPermission(permissions, required),
    hasAllPermissions: (required?: string[]) => hasAllPermissions(permissions, required),
    hasPermissionMeta: (meta: PermissionMeta) => hasPermissionMeta(permissions, meta),
  }
}
