import { afterEach, describe, expect, it, vi } from "vitest";
// @ts-ignore -- Vitest runs in Node; this app intentionally omits Node typings.
import { readFileSync } from "node:fs";

import * as api from "../../../../vendor/systutor-core/plugins/domains/frontend/dns/api";
import { useAuthStore } from "../features/auth/store";

afterEach(() => {
  vi.unstubAllGlobals();
  useAuthStore.setState({ token: null });
});

describe("Domains DNS API contract", () => {
  it("keeps DNS access on rows and uses the exact header hint", () => {
    const source = readFileSync(new URL(
      "../../../../vendor/systutor-core/plugins/domains/frontend/pages/DomainsPage.tsx",
      import.meta.url,
    ), "utf8");
    const table = readFileSync(new URL(
      "../../../../vendor/systutor-core/plugins/domains/frontend/components/DomainsTable.tsx",
      import.meta.url,
    ), "utf8");
    expect(source).not.toMatch(/<Button[^>]*>\s*\{t\("DNS"\)\}\s*<\/Button>/);
    expect(source).toContain('t("Double-click a domain to access its DNS zone.")');
    const rows = table.slice(table.indexOf("{rows.map"));
    expect(rows.indexOf("item.owner")).toBeLessThan(rows.indexOf("<CertificateCell"));
    expect(rows.indexOf("<CertificateCell")).toBeLessThan(rows.indexOf("<DnsStatusCell"));
    expect(table).toContain("groupRegisteredDomains(items)");
    expect(table).toContain("aria-expanded={expanded}");
  });

  it("keeps query keys DNS and zone scoped", () => {
    expect(api.dnsKeys.zones).toEqual(["domains", "dns", "zones"]);
    expect(api.dnsKeys.zone("example.com")).toEqual(["domains", "dns", "zones", "example.com"]);
  });

  it("uses authenticated raw text fetch for zone export", async () => {
    useAuthStore.setState({ token: "contract-test-token" });
    const fetchMock = vi.fn().mockResolvedValue(new Response("example.com. 300 IN A 192.0.2.1\n", { status: 200, headers: { "content-type": "text/plain" } }));
    vi.stubGlobal("fetch", fetchMock);
    const blob = await api.fetchZoneExport("example.com");
    expect(await blob.text()).toContain("example.com.");
    expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/plugins/domains/dns/zones/example.com/export");
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer contract-test-token");
  });

  it("sends RRset apply and delete expected versions to plugin-only paths", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ name: "example.com", kind: "Native", version: "v2", rrsets: [] }), { status: 200, headers: { "content-type": "application/json" } })));
    vi.stubGlobal("fetch", fetchMock);
    const draft = { name: "www", type: "A", ttl: 300, values: ["192.0.2.1"] };
    await api.applyRrsets("example.com", [draft], "v1");
    await api.deleteRrset("example.com", "www", "A", "v2");
    const calls = fetchMock.mock.calls;
    expect(calls[0][0]).toContain("/api/v1/plugins/domains/dns/zones/example.com/rrsets/apply");
    expect(JSON.parse(calls[0][1].body).expected_version).toBe("v1");
    expect(calls[1][0]).toContain("/api/v1/plugins/domains/dns/zones/example.com/rrsets/delete");
    expect(JSON.parse(calls[1][1].body).expected_version).toBe("v2");
    expect(calls.every(([url]) => !String(url).includes("/servers/localhost"))).toBe(true);
  });

  it("sends an explicit nameserver zone list, including empty bootstrap", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({}), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await api.applyNameservers({ settings: { ns1: "ns1.example", ns2: "ns2.example", ns1_ipv4: null, ns1_ipv6: null, ns2_ipv4: null, ns2_ipv6: null, soa_contact: "hostmaster.example", default_ttl: 300 }, zones: [], zone_versions: {}, expected_version: 0 });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).zones).toEqual([]);
  });
});
