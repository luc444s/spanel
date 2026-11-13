import type { PluginNavigationItem } from "@systutor/sdk/frontend";

import type { ShellNavSection } from "../shell/navigation";

export function firstSelectablePath(sections: ShellNavSection[]): string | null {
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

export function resolveLandingPath(
  permissions: string[],
  pluginNavigation: PluginNavigationItem[],
  buildSections: (input: {
    permissions: string[];
    pluginNavigation: PluginNavigationItem[];
  }) => ShellNavSection[],
): string {
  const sections = buildSections({ permissions, pluginNavigation });
  return firstSelectablePath(sections) ?? "/app/dashboard";
}

export function defaultLandingPath(
  permissions: string[],
  pluginNavigation: PluginNavigationItem[] = [],
): string {
  if (pluginNavigation.length === 0 && !permissions.includes("core.plugin.manage") && !permissions.some((p) => p.startsWith("core."))) {
    return "/app/dashboard";
  }
  // When plugin navigation is unknown, fall back to dashboard; router/runtime resolves accurately.
  return "/app/dashboard";
}
