import { DomainsView } from './DomainsView'

export function registerPlugin() {
  return {
    pluginId: 'proxy',
    routes: [
      { path: 'domains', element: <DomainsView />, requiredAnyPermissions: ['proxy.domains.read'] },
    ],
    navigation: [{ label: 'Dominios', to: '/p/proxy/domains', requiredAnyPermissions: ['proxy.domains.read'] }],
    widgets: [],
  }
}
