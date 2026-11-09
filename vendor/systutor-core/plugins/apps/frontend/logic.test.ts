import { describe, expect, it, vi } from "vitest";

import {
  canPlatformMutate, groupedFindings, hasBlockingFindings, isTerminalOperation,
  validateGithubAuthorizationUrl,
} from "./logic";
import type { Finding } from "./types";

const finding = (severity: Finding["severity"]): Finding => ({
  severity, code: `test.${severity}`, service: "web", path: "Dockerfile", line: 1,
  message: "message", remediation: "fix it",
});

describe("Apps frontend safety logic", () => {
  it("stops polling for every terminal status and not while queued", () => {
    expect(isTerminalOperation({ status: "succeeded" })).toBe(true);
    expect(isTerminalOperation({ status: "failed" })).toBe(true);
    expect(isTerminalOperation({ status: "interrupted" })).toBe(true);
    expect(isTerminalOperation({ status: "queued" })).toBe(false);
  });

  it("requires both the Apps permission and platform permission", () => {
    expect(canPlatformMutate(["apps.deployments.create"], "apps.deployments.create")).toBe(false);
    expect(canPlatformMutate(["core.platform.admin"], "apps.deployments.create")).toBe(false);
    expect(canPlatformMutate(["core.platform.admin", "apps.deployments.create"], "apps.deployments.create")).toBe(true);
  });

  it("groups findings and treats every error as blocking", () => {
    const findings = [finding("warning"), finding("error"), finding("info")];
    expect(groupedFindings(findings).error).toHaveLength(1);
    expect(hasBlockingFindings(findings)).toBe(true);
    expect(hasBlockingFindings([finding("warning")])).toBe(false);
  });

  it("accepts only an HTTPS github.com authorization host", () => {
    vi.stubGlobal("window", { location: { origin: "https://panel.example" } });
    expect(validateGithubAuthorizationUrl("https://github.com/login/oauth/authorize?client_id=x"))
      .toContain("https://github.com/");
    expect(() => validateGithubAuthorizationUrl("https://github.com.evil.test/login")).toThrow();
    expect(() => validateGithubAuthorizationUrl("http://github.com/login")).toThrow();
    vi.unstubAllGlobals();
  });
});
