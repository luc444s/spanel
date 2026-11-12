import { describe, expect, it } from "vitest";

import {
  buildNameserverApply,
  canApplyNameserverPlan,
  canConfigureNameservers,
  describePlanChanges,
  dnsStatus,
  longestOwnedZone,
  relativeOwner,
  shouldOpenDnsRow,
  validateRrset,
} from "../../../../vendor/systutor-core/plugins/domains/frontend/dns/logic";

const zones = [
  { name: "example.com", kind: "Native", version: "v1" },
  { name: "deep.example.com", kind: "Native", version: "v2" },
];

describe("Domains DNS pure policy", () => {
  it("resolves one zone list using the longest owned suffix", () => {
    expect(longestOwnedZone("www.deep.example.com", zones)?.name).toBe("deep.example.com");
    expect(dnsStatus("www.example.com", zones)).toBe("managed");
    expect(dnsStatus("outside.test", zones)).toBe("external");
    expect(dnsStatus("outside.test", [])).toBe("no-zone");
    expect(dnsStatus("outside.test", [], true)).toBe("error");
  });

  it("opens only owned rows with double-click or Enter outside controls", () => {
    expect(shouldOpenDnsRow(true, "TD")).toBe(true);
    expect(shouldOpenDnsRow(true, "TR", "Enter")).toBe(true);
    expect(shouldOpenDnsRow(true, "BUTTON")).toBe(false);
    expect(shouldOpenDnsRow(true, "A", "Enter")).toBe(false);
    expect(shouldOpenDnsRow(false, "TD")).toBe(false);
    expect(shouldOpenDnsRow(true, "TR", "Space")).toBe(false);
  });

  it("double-gates nameserver configuration", () => {
    expect(canConfigureNameservers(["domains.dns.nameservers"])).toBe(false);
    expect(canConfigureNameservers(["core.platform.admin"])).toBe(false);
    expect(canConfigureNameservers(["domains.dns.nameservers", "core.platform.admin"])).toBe(true);
  });

  it("validates RRset shape and invalidates malformed MX/CNAME", () => {
    expect(validateRrset({ name: "@", type: "A", ttl: 300, values: ["192.0.2.1"] })).toEqual({});
    expect(validateRrset({ name: "www", type: "CNAME", ttl: 0, values: ["a", "b"] }))
      .toMatchObject({ ttl: expect.any(String), values: expect.any(String) });
    expect(validateRrset({ name: "@", type: "MX", ttl: 300, values: ["mail.example"] }).values)
      .toBeTruthy();
  });

  it("presents absolute owners relative to their zone", () => {
    expect(relativeOwner("example.com", "example.com")).toBe("@");
    expect(relativeOwner("example.com", "www.example.com")).toBe("www");
    expect(relativeOwner("example.com", "outside.test")).toBe("outside.test");
    expect(relativeOwner("example.com.", "www.example.com.")).toBe("www");
  });

  it("describes plan changes as deterministic readable lines", () => {
    expect(describePlanChanges([
      { action: "change", name: "www.example.com", type: "A", ttl: 300, records: ["192.0.2.2"] },
      { action: "add", name: "mail.example.com", type: "MX", ttl: 600, records: ["20 mx2.example", "10 mx1.example"] },
    ])).toEqual([
      "+ mail.example.com MX 600 10 mx1.example | 20 mx2.example",
      "~ www.example.com A 300 192.0.2.2",
    ]);
  });

  it("always builds an explicit approved zone set and matching versions", () => {
    const settings = { ns1: "ns1.example", ns2: "ns2.example", ns1_ipv4: null, ns1_ipv6: null, ns2_ipv4: null, ns2_ipv6: null, soa_contact: "hostmaster.example", default_ttl: 300 };
    const plan = { settings_version: 4, settings_changed: true, settings_fields: [], zones: [], zone_versions: { "example.com": "v1" }, drift: true, warning: "warning" };
    expect(buildNameserverApply(settings, plan, [])).toMatchObject({ zones: [], zone_versions: {}, expected_version: 4 });
    expect(buildNameserverApply(settings, plan, ["example.com"]).zone_versions).toEqual({ "example.com": "v1" });
    expect(canApplyNameserverPlan(plan, [], true)).toBe(false);
    expect(canApplyNameserverPlan(plan, [], false)).toBe(true);
  });
});
