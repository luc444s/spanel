import { DomainsView } from './DomainsView'

export function registerPlugin() {
  return {
    pluginId: 'proxy',
    routes: [
      { path: 'domains', element: <DomainsView /> },
    ],
    navigation: [{ label: 'Dominios', to: '/p/proxy/domains' }],
    widgets: [],
  }
}
