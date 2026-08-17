import { SiteCreateView } from './SiteCreateView'
import { SiteDetailView } from './SiteDetailView'
import { SitesView } from './SitesView'

export function registerPlugin() {
  return {
    pluginId: 'hosting',
    routes: [
      { path: 'sites', element: <SitesView />, requiredAnyPermissions: ['hosting.sites.read'] },
      { path: 'sites/new', element: <SiteCreateView />, requiredAnyPermissions: ['hosting.sites.provision'] },
      { path: 'sites/:id', element: <SiteDetailView />, requiredAnyPermissions: ['hosting.sites.read'] },
    ],
    navigation: [{ label: 'Sitios', to: '/p/hosting/sites', requiredAnyPermissions: ['hosting.sites.read'] }],
    widgets: [],
  }
}
