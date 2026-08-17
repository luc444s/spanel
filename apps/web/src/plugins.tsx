import { useEffect, useState } from 'react'
import { hasPermissionMeta, type PermissionMeta } from './authz'

export type PluginNav = { label: string; to: string } & PermissionMeta
export type PluginRoute = { path: string; element: React.ReactNode } & PermissionMeta
export type PluginFrontend = {
  pluginId: string
  routes: PluginRoute[]
  navigation: PluginNav[]
}

type Registry = {
  navigation: PluginNav[]
  routes: PluginRoute[]
}

const pluginModules: Array<() => Promise<Record<string, unknown>>> = [
  () => import('@spanel-plugin/hosting'),
  () => import('@spanel-plugin/docker_infra'),
  () => import('@spanel-plugin/proxy'),
  () => import('@spanel-plugin/mail'),
]

export function usePluginRegistry(permissions: string[]): Registry {
  const [registry, setRegistry] = useState<Registry>({ navigation: [], routes: [] })

  useEffect(() => {
    if (permissions.length === 0) {
      setRegistry({ navigation: [], routes: [] })
      return
    }

    let cancelled = false
    void (async () => {
      try {
        const modules = await Promise.all(pluginModules.map(async (loader) => {
          try {
            const mod = await loader()
            return mod.registerPlugin ? (mod.registerPlugin as () => PluginFrontend)() : null
          } catch {
            return null
          }
        }))
        if (cancelled) return
        const loaded = modules.filter(Boolean) as PluginFrontend[]
        setRegistry({
          navigation: loaded.flatMap((m) => m.navigation.filter((item) => hasPermissionMeta(permissions, item))),
          routes: loaded.flatMap((m) =>
            m.routes
              .filter((route) => hasPermissionMeta(permissions, route))
              .map((route) => ({
                path: `/p/${m.pluginId}/${route.path}`,
                element: route.element,
              })),
          ),
        })
      } catch {
        if (!cancelled) setRegistry({ navigation: [], routes: [] })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [permissions.join('|')])

  return registry
}
