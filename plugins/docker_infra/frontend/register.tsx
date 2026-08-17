import { ContainersDashboardView } from './ContainersDashboardView'

export function registerPlugin() {
  return {
    pluginId: 'docker_infra',
    routes: [{ path: 'containers', element: <ContainersDashboardView />, requiredAnyPermissions: ['docker_infra.containers.read'] }],
    navigation: [{ label: 'Infra', to: '/p/docker_infra/containers', requiredAnyPermissions: ['docker_infra.containers.read'] }],
    widgets: [],
  }
}
