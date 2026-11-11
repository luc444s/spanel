import { describe, expect, it } from "vitest";

import {
  githubCardContent,
  githubCardState,
  validateGithubInstallUrl,
  validateGithubManifestUrl,
  validateGithubOrganization,
} from "../../../../vendor/systutor-core/plugins/apps/frontend/logic";
import type { GithubStatus } from "../../../../vendor/systutor-core/plugins/apps/frontend/types";

const status = (state: GithubStatus["state"]): GithubStatus => ({
  state,
  configured: true,
  connected: state === "connected",
  reconnect_required: false,
});

describe("Apps GitHub card state", () => {
  it("renders one exclusive state and never an error next to disconnected", () => {
    expect(githubCardState({ loading: true, error: true, registering: false })).toBe("loading");
    expect(githubCardState({
      loading: false, error: true, registering: false,
      status: status("unconfigured"),
    })).toBe("error");
    expect(githubCardState({
      loading: false, error: false, registering: true,
      status: status("unconfigured"),
    })).toBe("registering");
    for (const value of ["unconfigured", "pending_installation", "syncing", "connected", "suspended"] as const) {
      expect(githubCardState({ loading: false, error: false, registering: false, status: status(value) }))
        .toBe(value);
    }
  });

  it("renders exactly one error surface and no disconnected label on error", () => {
    const error = githubCardContent("error");
    expect(error.label).toBeNull();
    expect(error.alert).toBe("Could not read GitHub status");
    expect(error.alert).not.toContain("Not connected");
    const disconnected = githubCardContent("unconfigured");
    expect(disconnected.label).toBe("Not connected");
    expect(disconnected.alert).toBeNull();
    for (const state of ["loading", "registering", "pending_installation", "syncing", "connected", "suspended"] as const) {
      expect(githubCardContent(state).alert).toBeNull();
    }
  });
});

describe("GitHub manifest navigation guard", () => {
  it("accepts only the two exact GitHub manifest hosts", () => {
    expect(validateGithubManifestUrl("https://github.com/settings/apps/new", null))
      .toBe("https://github.com/settings/apps/new");
    expect(validateGithubManifestUrl(
      "https://github.com/organizations/acme/settings/apps/new", "acme",
    )).toBe("https://github.com/organizations/acme/settings/apps/new");
  });

  it("rejects spoofed hosts, paths, credentials and queries", () => {
    for (const value of [
      "https://github.com.evil.test/settings/apps/new",
      "http://github.com/settings/apps/new",
      "https://github.com:8443/settings/apps/new",
      "https://user@github.com/settings/apps/new",
      "https://github.com/settings/apps/new?next=evil",
      "https://github.com/organizations/acme/other",
      "https://github.com/organizations/acme/settings/apps/new",
    ]) {
      expect(() => validateGithubManifestUrl(value, null)).toThrow();
    }
    expect(() => validateGithubManifestUrl(
      "https://github.com/organizations/other/settings/apps/new", "acme",
    )).toThrow();
  });

  it("accepts only exact GitHub installation URLs", () => {
    expect(validateGithubInstallUrl("https://github.com/apps/spanel-apps-1/installations/new"))
      .toBe("https://github.com/apps/spanel-apps-1/installations/new");
    for (const value of [
      "https://github.com.evil.test/apps/x/installations/new",
      "http://github.com/apps/x/installations/new",
      "https://github.com/apps/x/installations/new?next=evil",
      "https://github.com/apps/x/installations/other",
    ]) {
      expect(() => validateGithubInstallUrl(value)).toThrow();
    }
  });

  it("validates organization slugs", () => {
    expect(validateGithubOrganization(" acme ")).toBe("acme");
    expect(() => validateGithubOrganization("../acme")).toThrow();
    expect(() => validateGithubOrganization("acme/settings")).toThrow();
  });
});
