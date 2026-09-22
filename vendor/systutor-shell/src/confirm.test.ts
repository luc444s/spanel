import { describe, it, expect } from "vitest";
import { ConfirmContext, isConfirmAction } from "./confirm";

describe("ConfirmContext", () => {
  it("exports ConfirmContext with null default", () => {
    expect(ConfirmContext).toBeDefined();
  });

  it("isConfirmAction detects valid confirm actions", () => {
    const action = {
      _confirm: true,
      previewResult: "preview",
      confirmMessage: "test",
      execute: () => Promise.resolve("ok"),
    };
    expect(isConfirmAction(action)).toBe(true);
  });

  it("isConfirmAction rejects normal objects", () => {
    expect(isConfirmAction({ foo: "bar" })).toBe(false);
    expect(isConfirmAction(null)).toBe(false);
    expect(isConfirmAction("string")).toBe(false);
  });
});
