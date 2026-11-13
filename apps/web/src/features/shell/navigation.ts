import type { PluginNavigationItem } from "@systutor/sdk/frontend";

import { t } from "../../i18n";

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
    systemItems.push({ kind: "link", label: t("Dashboard"), to: "/app/dashboard" });
    systemItems.push({ kind: "link", label: t("Plugins"), to: "/app/plugins" });
  }
  if (systemItems.length > 0) {
    sections.push({ title: t("System"), items: systemItems });
  }

  const settingsItems: Array<ShellNavLinkItem | ShellNavActionItem> = [];
  if (permissions.includes("core.users.read")) {
    settingsItems.push({ kind: "link", label: t("Users"), to: "/app/settings/users" });
  }
  if (
    permissions.includes("core.roles.read") ||
    permissions.includes("core.roles.manage")
  ) {
    settingsItems.push({ kind: "link", label: t("Roles"), to: "/app/settings/roles" });
  }
  if (
    permissions.includes("core.branches.read") ||
    permissions.includes("core.branches.manage")
  ) {
    settingsItems.push({ kind: "link", label: t("Branches"), to: "/app/settings/branches" });
  }
  if (settingsItems.length > 0) {
    sections.push({ title: t("Settings"), items: settingsItems });
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
      const sortedChildren = [...group.children].sort((a, b) => {
        const adminGroup = a.group === "Administration" || b.group === "Administration";
        if (adminGroup) {
          const aDomains = a.to === "/app/domains" ? 0 : 1;
          const bDomains = b.to === "/app/domains" ? 0 : 1;
          if (aDomains !== bDomains) return aDomains - bDomains;
        }
        return 0;
      });

      if (group.parent && group.children.length > 0) {
        sections.push({
          title: t(group.parent.label),
          items: [
            { kind: "link", label: t(group.parent.label), to: group.parent.to },
            {
              kind: "group",
              label: group.parent.label,
              to: group.parent.to,
              items: sortedChildren.map((child) => ({
                kind: "link" as const,
                label: t(child.label),
                to: child.to,
              })),
            },
          ],
        });
      } else if (group.children.length > 0) {
        sections.push({
          title: t(group.children[0].group!),
          items: [{
            kind: "group",
            label: t(group.children[0].group!),
            to: "",
            items: sortedChildren.map((child) => ({
              kind: "link" as const,
              label: t(child.label),
              to: child.to,
            })),
          }],
        });
      } else if (group.parent) {
        sections.push({
          title: t(group.parent.label),
          items: [{ kind: "link", label: t(group.parent.label), to: group.parent.to }],
        });
      }
    }
  }

  sections.push({
    title: t("Session"),
    items: [{ kind: "action", label: t("Sign out"), action: "logout" }],
  });

  const logisticsIndex = sections.findIndex((s) => s.title === t("LOGISTICS"));
  if (logisticsIndex > 0) {
    const [logistics] = sections.splice(logisticsIndex, 1);
    sections.unshift(logistics);
  }

  return sections;
}

export function firstSelectableSidebarPath(sections: ShellNavSection[]): string | null {
  for (const section of sections) {
    for (const item of section.items) {
      if (item.kind === "link") return item.to;
      if (item.kind === "group") {
        const first = item.items.find((child) => child.kind === "link");
        if (first) return first.to;
      }
    }
  }
  return null;
}
