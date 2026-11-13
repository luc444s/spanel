import { describe, expect, it } from "vitest";

import { resolveLandingPath } from "../features/auth/landing";
import { buildShellSidebarSections, firstSelectableSidebarPath } from "../features/shell/navigation";

describe("Sidebar landing and ordering", () => {
  it("lands on the first selectable sidebar option", () => {
    expect(
      resolveLandingPath(
        ["domains.domains.read"],
        [
          { to: "/app/domains", label: "Domains and DNS", group: "Administration" },
          { to: "/app/apps", label: "Apps", group: "Administration" },
        ],
        buildShellSidebarSections,
      ),
    ).toBe("/app/domains");

    expect(resolveLandingPath(["core.plugin.manage"], [], buildShellSidebarSections)).toBe("/app/dashboard");
    expect(resolveLandingPath([], [], buildShellSidebarSections)).toBe("/app/dashboard");
  });

  it("prioritizes Domains and DNS first in Administration", () => {
    const sections = buildShellSidebarSections({
      permissions: [],
      pluginNavigation: [
        { to: "/app/apps", label: "Apps", group: "Administration" },
        { to: "/app/hosting", label: "Hosting", group: "Administration" },
        { to: "/app/domains", label: "Domains and DNS", group: "Administration" },
      ],
    });
    const administration = sections.find((section) => section.title === "Administration");
    const group = administration?.items[0];
    expect(group?.kind).toBe("group");
    if (group?.kind === "group") {
      expect(group.items[0]).toMatchObject({ to: "/app/domains", label: "Domains and DNS" });
      expect(firstSelectableSidebarPath(sections)).toBe("/app/domains");
    }
  });
});
