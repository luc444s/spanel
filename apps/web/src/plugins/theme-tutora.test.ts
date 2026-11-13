import { describe, expect, it } from "vitest";

import { themes } from "@systutor/themes";

describe("Tutora theme", () => {
  it("keeps every elevated surface flat and square", () => {
    const tutora = themes.tutora;

    expect(tutora.radius).toBe("0");
    expect(tutora.enforceRadius).toBe("none");
    expect(tutora.enforceShadow).toBe("none");
    expect(tutora.cardShadow).toBe("0 0 0 0 transparent");
    expect(tutora.tableBackground).toBe("transparent");

    for (const surface of [
      tutora.card,
      tutora.popover,
      tutora.secondary,
      tutora.muted,
      tutora.surface,
      tutora.surfaceAlt,
    ]) {
      expect(surface).toBe(tutora.background);
    }
  });

  it("shifts the complete Catppuccin Mocha hierarchy into orange", () => {
    const tutoraMocha = themes.tutora_catpuccin_mocha;

    expect(tutoraMocha.background).toBe("20 24% 14%");
    expect(tutoraMocha.card).toBe("21 21% 21%");
    expect(tutoraMocha.secondary).toBe("23 22% 29%");
    expect(tutoraMocha.surfaceAlt).toBe("22 23% 18%");
    expect(tutoraMocha.primary).toBe("22 86% 72%");
    expect(tutoraMocha.ring).toBe(tutoraMocha.primary);
    expect(tutoraMocha.primary).not.toBe(themes.tutora.primary);
    expect(tutoraMocha.enforceRadius).toBe("none");
    expect(tutoraMocha.enforceShadow).toBe("none");
  });
});
