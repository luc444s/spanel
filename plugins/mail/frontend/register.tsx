import { MailView } from './MailView'

export function registerPlugin() {
  return {
    pluginId: 'mail',
    routes: [
      { path: 'mail', element: <MailView /> },
    ],
    navigation: [{ label: 'Mail', to: '/p/mail/mail' }],
    widgets: [],
  }
}
