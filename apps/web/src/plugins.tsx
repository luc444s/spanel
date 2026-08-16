import { useEffect, useState } from 'react'
import { apiRequest } from '@systutor/shell'
import type { PluginRuntimeRecord } from '@systutor/shell'

export type PluginNav = { label: string; to: string }
export type PluginRoute = { path: string; element: React.ReactNode }
export type PluginFrontend = {
  pluginId: string
  routes: PluginRoute[]
  navigation: PluginNav[]
}

type Registry = {
  navigation: PluginNav[]
  routes: PluginRoute[]
}

const pluginModules: Record<string, () => Promise<Record<string, unknown>>> = {
  hosting: () => import('@spanel-plugin/hosting'),
  docker_infra: () => import('@spanel-plugin/docker_infra'),
  proxy: () => import('@spanel-plugin/proxy'),
  mail: () => import('@spanel-plugin/mail'),
}

export function usePluginRegistry(): Registry {
  const [registry, setRegistry] = useState<Registry>({ navigation: [], routes: [] })

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const plugins = await apiRequest<PluginRuntimeRecord[]>('/api/v1/core/plugins')
        const enabled = plugins.filter((p) => p.is_enabled && p.frontend_entrypoint)
        const modules = await Promise.all(
          enabled.map(async (plugin) => {
            const loader = pluginModules[plugin.plugin_id]
            if (!loader) return null
            try {
              const mod = await loader()
              const reg = mod.registerPlugin ? (mod.registerPlugin as () => PluginFrontend)() : null
              return reg && reg.pluginId === plugin.plugin_id ? reg : null
            } catch {
              return null
            }
          }),
        )
        if (cancelled) return
        const loaded = modules.filter(Boolean) as PluginFrontend[]
        setRegistry({
          navigation: loaded.flatMap((m) => m.navigation),
          routes: loaded.flatMap((m) =>
            m.routes.map((route) => ({
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
  }, [])

  return registry
}
