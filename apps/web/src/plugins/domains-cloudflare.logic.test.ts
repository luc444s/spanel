import { describe, expect, it } from "vitest";

import {
  CLOUDFLARE_FLOW_MAX_MS,
  canUseCloudflare,
  cloudflareErrorMessage,
  errorMessageFrom,
  flowDeadline,
  isCloudflareConnected,
  pollDecision,
  zoneCount,
} from "../../../../vendor/systutor-core/plugins/domains/frontend/cloudflare/logic";

const connected = {
  connected: true,
  tenant_id: "t1",
  account_id: "acct",
  account_name: "Lab",
  scopes: ["Zone Read", "DNS Read", "DNS Write"],
  expires_at: null,
  status: "active",
  updated_at: null,
};

describe("Cloudflare panel policy", () => {
  it("is visible only with the plugin permission", () => {
    expect(canUseCloudflare(["domains.dns.read"])).toBe(false);
    expect(canUseCloudflare(["domains.dns.cloudflare"])).toBe(true);
  });

  it("treats only an active connection as connected", () => {
    expect(isCloudflareConnected(null)).toBe(false);
    expect(isCloudflareConnected({ ...connected, connected: false })).toBe(false);
    expect(isCloudflareConnected({ ...connected, status: "disconnected" })).toBe(false);
    expect(isCloudflareConnected(connected)).toBe(true);
  });

  it("clamps the polling deadline to the backend expiry and to five minutes", () => {
    const now = Date.parse("2026-11-12T12:00:00Z");
    expect(flowDeadline(null, now)).toBe(now + CLOUDFLARE_FLOW_MAX_MS);
    expect(flowDeadline("2026-11-12T12:01:00Z", now)).toBe(now + 60_000);
    expect(flowDeadline("2026-11-12T12:30:00Z", now)).toBe(now + CLOUDFLARE_FLOW_MAX_MS);
    expect(flowDeadline("not-a-date", now)).toBe(now + CLOUDFLARE_FLOW_MAX_MS);
  });

  it("stops polling only when connected or expired", () => {
    const deadline = 1_000;
    expect(pollDecision(null, deadline, 500)).toBe("continue");
    expect(pollDecision(connected, deadline, 500)).toBe("connected");
    expect(pollDecision(null, deadline, 1_000)).toBe("expired");
  });

  it("maps normalized HTTP statuses to user messages without leaking bodies", () => {
    expect(cloudflareErrorMessage(403)).toMatch(/permission/);
    expect(cloudflareErrorMessage(404)).toMatch(/not connected/);
    expect(cloudflareErrorMessage(409)).toMatch(/pending/);
    expect(cloudflareErrorMessage(422)).toMatch(/configured/);
    expect(cloudflareErrorMessage(429)).toMatch(/rate limiting/);
    expect(cloudflareErrorMessage(502)).toMatch(/invalid response/);
    expect(cloudflareErrorMessage(503)).toMatch(/unavailable/);
    expect(cloudflareErrorMessage(504)).toMatch(/did not respond/);
    expect(cloudflareErrorMessage(null)).toMatch(/Could not reach/);
  });

  it("extracts status and code from thrown API errors", () => {
    expect(errorMessageFrom({ status: 403, code: "domains.dns.cloudflare.forbidden" }))
      .toMatch(/permission/);
    expect(errorMessageFrom(new Error("secret-canary"))).not.toMatch(/secret-canary/);
  });

  it("counts zones defensively", () => {
    expect(zoneCount(undefined)).toBe(0);
    expect(zoneCount([])).toBe(0);
    expect(zoneCount([{ id: "1" }, { id: "2" }])).toBe(2);
  });
});
