import { describe, expect, it, vi } from "vitest";

import {
  canPlatformMutate, groupedFindings, hasBlockingFindings, isTerminalOperation, parseDotenv,
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

  it("parses pasted dotenv text without splitting values on equals", () => {
    expect(parseDotenv(`
# deployment values
APP_DB_NAME=editors
export DATABASE_URL=postgresql://user:p=a=s=s@database/editors
EMPTY=
`)).toEqual([
      { name: "APP_DB_NAME", value: "editors" },
      { name: "DATABASE_URL", value: "postgresql://user:p=a=s=s@database/editors" },
      { name: "EMPTY", value: "" },
    ]);
  });

  it("rejects invalid and duplicate dotenv names", () => {
    expect(() => parseDotenv("BAD NAME=value")).toThrow("línea 1");
    expect(() => parseDotenv("API_KEY=one\nAPI_KEY=two")).toThrow("duplicada: API_KEY");
    expect(() => parseDotenv("APP_DB_PASSWORD=manual")).toThrow(
      "administra automáticamente APP_DB_PASSWORD",
    );
  });


});
