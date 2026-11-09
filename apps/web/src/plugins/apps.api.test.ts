import { describe, expect, it } from "vitest";

import * as api from "../../../../vendor/systutor-core/plugins/apps/frontend/api";

describe("Apps API module contract", () => {
  it("exports the installation repositories reader used by the UI", () => {
    expect(typeof api.listInstallationRepositories).toBe("function");
    expect("listRepositories" in api).toBe(false);
  });

  it("keeps repository query keys installation-scoped", () => {
    expect(api.appsKeys.repositories("installation-1")).toEqual([
      "apps", "github", "installations", "installation-1", "repositories",
    ]);
    expect(api.appsKeys.repositories("installation-2")).not.toEqual(
      api.appsKeys.repositories("installation-1"),
    );
  });
});
