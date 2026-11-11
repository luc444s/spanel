import { useQuery } from "@tanstack/react-query";

import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";

import { useT } from "@/i18n/use-i18n";

import { hostingKeys, hostingSiteLogs, listSiteUsers, type HostingSite, type WordPressUser } from "../api";

type UsersDialogProps = {
  site: HostingSite | null;
  canReset: boolean;
  onClose: () => void;
  onReset: (user: WordPressUser) => void;
};

/**
 * Users live in their own dialog on top of the site dialog. The query is
 * enabled by the open site, so closing the dialog also stops asking Docker.
 */
export function UsersDialog({ site, canReset, onClose, onReset }: UsersDialogProps) {
  const t = useT();
  const usersQuery = useQuery({
    queryKey: hostingKeys.users(site?.id ?? ""),
    queryFn: () => listSiteUsers(site!.id),
    enabled: Boolean(site),
    // Reading users costs five Docker commands over SSH; reopening the dialog
    // within this window reuses the cached rows instead of paying for them
    // again. Only WordPress changes this list, so a short window is safe.
    staleTime: 60_000,
  });
  const users = usersQuery.data?.users ?? [];

  return (
    <Dialog
      open={Boolean(site)}
      title={t("Users of {site}", { site: site?.container_name ?? "" })}
      description={site && !site.managed ? t("Site not manageable: read only") : undefined}
      onClose={onClose}
      zIndexClassName="z-[1100]"
      maxWidthClassName="max-w-3xl"
    >
      {usersQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">{t("Loading users...")}</p>
      ) : usersQuery.error ? (
        <Alert title={t("Could not load users")}>
          {usersQuery.error instanceof Error
            ? usersQuery.error.message
            : t("Unknown error while querying the site.")}
        </Alert>
      ) : users.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("The site has no users.")}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="pb-2 pr-4">{t("ID")}</th>
                <th className="pb-2 pr-4">{t("User")}</th>
                <th className="pb-2 pr-4">{t("Email")}</th>
                <th className="pb-2 pr-4">{t("Status")}</th>
                <th className="pb-2 pr-4">{t("Role")}</th>
                {site?.managed && canReset ? <th className="pb-2">{t("Actions")}</th> : null}
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id} className="border-b border-border/50">
                  <td className="py-2 pr-4 font-mono text-xs">{user.id}</td>
                  <td className="py-2 pr-4">{user.login}</td>
                  <td className="py-2 pr-4">{user.email}</td>
                  <td className="py-2 pr-4">{user.status === 0 ? t("Active") : t("Inactive")}</td>
                  <td className="py-2 pr-4">{user.role}</td>
                  {site?.managed && canReset ? (
                    <td className="py-2">
                      <Button size="sm" variant="secondary" onClick={() => onReset(user)}>
                        {t("Reset")}
                      </Button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Dialog>
  );
}

type LogsDialogProps = {
  site: HostingSite | null;
  onClose: () => void;
};

export function LogsDialog({ site, onClose }: LogsDialogProps) {
  const t = useT();
  const logsQuery = useQuery({
    queryKey: ["hosting", site?.id, "logs"],
    queryFn: () => hostingSiteLogs(site!.id),
    enabled: Boolean(site),
  });

  return (
    <Dialog
      open={Boolean(site)}
      title={t("Logs for {site}", { site: site?.container_name ?? "" })}
      onClose={onClose}
      zIndexClassName="z-[1100]"
      maxWidthClassName="max-w-3xl"
    >
      {logsQuery.error ? (
        <Alert title={t("Could not read logs")}>
          {logsQuery.error instanceof Error
            ? logsQuery.error.message
            : t("Unknown error while querying the site.")}
        </Alert>
      ) : (
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs">
          {logsQuery.data?.logs ?? t("Loading...")}
        </pre>
      )}
    </Dialog>
  );
}
