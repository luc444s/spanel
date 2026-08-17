import { SiteCreateView } from './SiteCreateView'
import { SiteDetailView } from './SiteDetailView'
import { SitesView } from './SitesView'

export function registerPlugin() {
  return {
    pluginId: 'hosting',
    routes: [
      { path: 'sites', element: <SitesView /> },
      { path: 'sites/new', element: <SiteCreateView /> },
      { path: 'sites/:id', element: <SiteDetailView /> },
    ],
    navigation: [{ label: 'Sitios', to: '/p/hosting/sites' }],
    widgets: [],
  }
}
