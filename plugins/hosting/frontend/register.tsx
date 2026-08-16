import { SiteDetailView } from './SiteDetailView'
import { SitesView } from './SitesView'

export function registerPlugin() {
  return {
    pluginId: 'hosting',
    routes: [
      { path: 'sites', element: <SitesView /> },
      { path: 'sites/:id', element: <SiteDetailView /> },
    ],
    navigation: [{ label: 'Sitios', to: '/p/hosting/sites' }],
    widgets: [],
  }
}
