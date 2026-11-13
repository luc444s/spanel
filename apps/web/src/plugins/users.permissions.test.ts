import { describe, expect, it } from "vitest";

import type { CoreRole, CoreUser } from "../features/core-management/api";
import { assignedRoleId, roleMemberCount } from "../features/settings/user-permissions";

const roles = [
  { id: "r1", tenant_id: "t1", name: "Administrator", active: true, permissions: [], created_at: "", updated_at: "" },
  { id: "r2", tenant_id: "t1", name: "Operator", active: true, permissions: [], created_at: "", updated_at: "" },
] satisfies CoreRole[];

function user(roleNames: string[]): CoreUser {
  return { id: "u1", name: "Lucas", email: "lucas@example.com", roles: roleNames } as CoreUser;
}

describe("tenant user role selection", () => {
  it("resolves the only assigned role", () => {
    expect(assignedRoleId(user(["Operator"]), roles)).toBe("r2");
  });

  it("requires an explicit choice for legacy multi-role users", () => {
    expect(assignedRoleId(user(["Administrator", "Operator"]), roles)).toBeNull();
    expect(assignedRoleId(user([]), roles)).toBeNull();
  });

  it("counts every user affected by a shared role edit", () => {
    const users = [user(["Operator"]), { ...user(["Operator"]), id: "u2" }, user(["Administrator"])];
    expect(roleMemberCount(roles[1], users)).toBe(2);
  });
});
