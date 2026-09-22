import type { PluginNavigationItem } from "@systutor/sdk/frontend";

export type ShellNavLinkItem = {
  kind: "link";
  label: string;
  to: string;
};

export type ShellNavActionItem = {
  kind: "action";
  label: string;
  action: "logout";
};

export type ShellNavGroupItem = {
  kind: "group";
  label: string;
  to: string;
  items: ShellNavLinkItem[];
};

export type ShellNavSection = {
  title: string;
  items: Array<ShellNavLinkItem | ShellNavActionItem | ShellNavGroupItem>;
};

type BuildShellSidebarSectionsInput = {
  permissions: string[];
  pluginNavigation: PluginNavigationItem[];
};

export function buildShellSidebarSections({
  permissions,
  pluginNavigation,
}: BuildShellSidebarSectionsInput): ShellNavSection[] {
  const sections: ShellNavSection[] = [];

  const systemItems: Array<ShellNavLinkItem | ShellNavActionItem> = [];
  if (permissions.includes("core.plugin.manage")) {
    systemItems.push({ kind: "link", label: "Dashboard", to: "/app/dashboard" });
    systemItems.push({ kind: "link", label: "Plugins", to: "/app/plugins" });
  }
  if (systemItems.length > 0) {
    sections.push({ title: "Sistema", items: systemItems });
  }

  const settingsItems: Array<ShellNavLinkItem | ShellNavActionItem> = [];
  if (permissions.includes("core.users.read")) {
    settingsItems.push({ kind: "link", label: "Usuarios", to: "/app/settings/users" });
  }
  if (permissions.includes("core.roles.read") || permissions.includes("core.roles.manage")) {
    settingsItems.push({ kind: "link", label: "Roles", to: "/app/settings/roles" });
  }
  if (
    permissions.includes("core.branches.read") ||
    permissions.includes("core.branches.manage")
  ) {
    settingsItems.push({ kind: "link", label: "Sucursales", to: "/app/settings/branches" });
  }
  if (settingsItems.length > 0) {
    sections.push({ title: "Ajustes", items: settingsItems });
  }

  if (pluginNavigation.length > 0) {
    const grouped = new Map<string, { parent?: PluginNavigationItem; children: PluginNavigationItem[] }>();

    for (const entry of pluginNavigation) {
      if (entry.group) {
        const g = grouped.get(entry.group) ?? { children: [] };
        g.children.push(entry);
        grouped.set(entry.group, g);
      } else {
        const g = grouped.get(entry.label) ?? { children: [] };
        g.parent = entry;
        grouped.set(entry.label, g);
      }
    }

    for (const [, group] of grouped) {
      if (group.parent && group.children.length > 0) {
        sections.push({
          title: group.parent.label,
          items: [
            { kind: "link", label: group.parent.label, to: group.parent.to },
            {
              kind: "group",
              label: group.parent.label,
              to: group.parent.to,
              items: group.children.map((child) => ({
                kind: "link" as const,
                label: child.label,
                to: child.to,
              })),
            },
          ],
        });
      } else if (group.children.length > 0) {
        sections.push({
          title: group.children[0].group!,
          items: [{
            kind: "group",
            label: group.children[0].group!,
            to: "",
            items: group.children.map((child) => ({
              kind: "link" as const,
              label: child.label,
              to: child.to,
            })),
          }],
        });
      } else if (group.parent) {
        sections.push({
          title: group.parent.label,
          items: [{ kind: "link", label: group.parent.label, to: group.parent.to }],
        });
      }
    }
  }

  sections.push({
    title: "Sesión",
    items: [{ kind: "action", label: "Cerrar sesión", action: "logout" }],
  });

  const logisticsIndex = sections.findIndex((s) => s.title === "LOGISTICS");
  if (logisticsIndex > 0) {
    const [logistics] = sections.splice(logisticsIndex, 1);
    sections.unshift(logistics);
  }

  return sections;
}
