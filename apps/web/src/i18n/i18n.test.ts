import { describe, expect, it } from "vitest";

import { translate } from "./index";
import es from "./es";

describe("i18n", () => {
  it("uses English as the source language", () => {
    expect(translate("Save changes", undefined, "en")).toBe("Save changes");
  });

  it("returns the Spanish translation when available", () => {
    expect(translate("Save changes", undefined, "es")).toBe("Guardar cambios");
  });

  it("falls back to English when a translation is missing", () => {
    expect(translate("A message without translation", undefined, "es")).toBe(
      "A message without translation",
    );
  });

  it("interpolates values", () => {
    expect(translate("Delete {count} users", { count: 3 }, "en")).toBe(
      "Delete 3 users",
    );
  });

  it("keeps a complete, non-empty Spanish catalog", () => {
    const empty = Object.entries(es).filter(([, value]) => !value.trim());
    expect(empty).toEqual([]);
  });
});
