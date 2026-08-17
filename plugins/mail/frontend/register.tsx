import { MailView } from './MailView'

export function registerPlugin() {
  return {
    pluginId: 'mail',
    routes: [
      {
        path: 'mail',
        element: <MailView />,
        requiredAnyPermissions: ['mail.server.read', 'mail.domains.read', 'mail.mailboxes.read'],
      },
    ],
    navigation: [{ label: 'Mail', to: '/p/mail/mail', requiredAnyPermissions: ['mail.server.read', 'mail.domains.read', 'mail.mailboxes.read'] }],
    widgets: [],
  }
}
