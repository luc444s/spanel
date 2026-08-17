import { ContainersDashboardView } from './ContainersDashboardView'

export function registerPlugin() {
  return {
    pluginId: 'docker_infra',
    routes: [{ path: 'containers', element: <ContainersDashboardView /> }],
    navigation: [{ label: 'Infra', to: '/p/docker_infra/containers' }],
    widgets: [],
  }
}
