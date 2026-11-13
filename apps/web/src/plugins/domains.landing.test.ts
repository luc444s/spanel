import { describe, expect, it } from "vitest";

import { defaultLandingPath } from "../features/auth/landing";
import { buildShellSidebarSections } from "../features/shell/navigation";

describe("Domains and DNS product priority", () => {
  it("is the landing page when the user can read domains", () => {
    expect(defaultLandingPath(["domains.domains.read"])).toBe("/app/domains");
    expect(defaultLandingPath([])).toBe("/app/dashboard");
  });

  it("is first inside Administration", () => {
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
      expect(group.items[0]).toMatchObject({
        to: "/app/domains", label: "Domains and DNS",
      });
    }
  });
});
