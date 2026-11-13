import { describe, expect, it } from "vitest";

import type { Operation, ServiceDetail } from "../../../../vendor/systutor-core/plugins/apps/frontend/types";
import {
  OPERATIONS_PAGE_SIZE, operationDuration, resultText, serviceFromSubject,
} from "../../../../vendor/systutor-core/plugins/apps/frontend/components/ServiceOperations";
import { routePayload, serviceRoutePreview, type RouteForm } from "../../../../vendor/systutor-core/plugins/apps/frontend/components/ServiceDomains";
import {
  canRevealVariable, remainingSeconds, revealExpiry, sourceLabel,
} from "../../../../vendor/systutor-core/plugins/apps/frontend/components/ServiceEnvironment";
import { statusDotTone } from "../../../../vendor/systutor-core/plugins/apps/frontend/components/StatusIndicator";

const service = {
  name: "api", image: "api:latest", status: "running", health: "healthy",
  ports: [8000], restart_count: 0, environment_names: [], environment: [], created_at: null,
} as ServiceDetail;

const form: RouteForm = {
  domain: "lab.example.com", path: "/api", internal_path: "/v1",
  strip_path: true, port: "8000", entrypoints: ["websecure"],
  https: true, middlewares: ["rate-limit@file"],
};

describe("Apps service console", () => {
  it("uses semantic colors for application and service states", () => {
    expect(statusDotTone("running")).toBe("text-emerald-500");
    expect(statusDotTone("stopped")).toBe("text-red-500");
    expect(statusDotTone("failed")).toBe("text-red-500");
    expect(statusDotTone("deploying")).toBe("text-amber-500");
    expect(statusDotTone("unknown")).toBe("text-zinc-400");
  });

  it("labels environment sources exactly as the API declares them", () => {
    expect(sourceLabel("managed")).toBe("Managed");
    expect(sourceLabel("tenant")).toBe("Tenant");
    expect(sourceLabel("image")).toBe("Image");
  });

  it("offers reveal only when the API says revealable and RBAC allows it", () => {
    const variable = { name: "X", source: "managed" as const, secret: true, revealable: true };
    expect(canRevealVariable(variable, true)).toBe(true);
    expect(canRevealVariable(variable, false)).toBe(false);
    expect(canRevealVariable({ ...variable, revealable: false }, true)).toBe(false);
    const maskedOnly = { ...variable, secret: true, revealable: false };
    expect(canRevealVariable(maskedOnly, true)).toBe(false);
  });

  it("expires a revealed value in at most thirty seconds", () => {
    const expires = revealExpiry(1_000);
    expect(expires).toBe(31_000);
    expect(remainingSeconds(expires, 1_000)).toBe(30);
    expect(remainingSeconds(expires, 31_000)).toBe(0);
  });

  it("builds the exact Domains payload without inventing host or options", () => {
    expect(routePayload(form, "a1:api")).toEqual({
      domain: "lab.example.com",
      path: "/api",
      internal_path: "/v1",
      strip_path: true,
      port: 8000,
      entrypoints: ["websecure"],
      https: true,
      middlewares: ["rate-limit@file"],
      site_id: "a1:api",
    });
    const update = routePayload(form) as Record<string, unknown>;
    expect(update.site_id).toBeUndefined();
    expect(update).not.toHaveProperty("host");
  });

  it("previews strip and internal path without changing the destination", () => {
    expect(serviceRoutePreview(form, service)).toEqual({
      external: "https://lab.example.com/api/*",
      target: "api:8000/v1/*",
      strip: "/api",
    });
    expect(serviceRoutePreview({ ...form, path: "/" }, service).strip).toBeNull();
  });

  it("shows operations for the service and only public result text", () => {
    const operation = {
      id: "op1", application_id: "a1", subject_type: "app_service",
      subject_id: "a1:api", kind: "restart", status: "succeeded", phase: "complete",
      report: null, result: { output: "reiniciado" }, error_code: null,
      public_error: null, created_at: null, started_at: null, finished_at: null,
    } as Operation;
    expect(serviceFromSubject(operation)).toBe("api");
    expect(resultText(operation)).toBe("reiniciado");
    expect(resultText({ ...operation, result: { hidden: "value" } })).toBe(
      "No public detail.",
    );
  });

  it("paginates operations at eight rows and formats durations compactly", () => {
    expect(OPERATIONS_PAGE_SIZE).toBe(8);
    const operation = {
      id: "op1", application_id: "a1", subject_type: "app_service",
      subject_id: "a1:api", kind: "restart", status: "succeeded", phase: "complete",
      report: null, result: null, error_code: null, public_error: null,
      created_at: null, started_at: "2026-01-01T00:00:00Z",
      finished_at: "2026-01-01T00:00:01.5Z",
    } as Operation;
    expect(operationDuration(operation)).toBe("1.5 s");
    expect(operationDuration({ ...operation, finished_at: null })).toBe("—");
  });
});
