import { describe, expect, it } from "vitest";

import { defaultLandingPath } from "../features/auth/landing";
import { buildShellSidebarSections, firstSelectableSidebarPath } from "../features/shell/navigation";

describe("Sidebar landing and ordering", () => {
  it("lands on the first selectable sidebar option", () => {
    expect(
      defaultLandingPath(["domains.domains.read"], [
        { to: "/app/domains", label: "Domains and DNS", group: "Administration" },
        { to: "/app/apps", label: "Apps", group: "Administration" },
      ]),
    ).toBe("/app/domains");

    expect(
      defaultLandingPath(["core.plugin.manage"], []),
    ).toBe("/app/dashboard");

    expect(defaultLandingPath([], [])).toBe("/app/dashboard");
  });

  it("keeps plugin navigation order as provided", () => {
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
      expect(group.items[0]).toMatchObject({ to: "/app/apps", label: "Apps" });
      expect(firstSelectableSidebarPath(sections)).toBe("/app/apps");
    }
  });
});
