import { SitesView } from './SitesView'

export function registerPlugin() {
  return {
    pluginId: 'hosting',
    routes: [{ path: 'sites', element: <SitesView /> }],
    navigation: [{ label: 'Sitios', to: '/p/hosting/sites' }],
    widgets: [],
  }
}
