import { describe, it, expect } from "vitest";
import { isNeofetchCommand } from "./neofetch";

describe("isNeofetchCommand", () => {
  it("detects neofetch", () => {
    expect(isNeofetchCommand("neofetch")).toBe(true);
  });

  it("detects sysinfo", () => {
    expect(isNeofetchCommand("sysinfo")).toBe(true);
  });

  it("is case-insensitive", () => {
    expect(isNeofetchCommand("NEOFETCH")).toBe(true);
  });

  it("does not flag normal commands", () => {
    expect(isNeofetchCommand("cotizar cliente Bohdan")).toBe(false);
  });
});
