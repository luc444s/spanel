import type { PluginNavigationItem } from "@systutor/sdk/frontend";

import { buildShellSidebarSections, firstSelectableSidebarPath } from "../shell/navigation";

export function defaultLandingPath(
  permissions: string[],
  pluginNavigation: PluginNavigationItem[] = [],
): string {
  const sections = buildShellSidebarSections({ permissions, pluginNavigation });
  return firstSelectableSidebarPath(sections) ?? "/app/dashboard";
}
