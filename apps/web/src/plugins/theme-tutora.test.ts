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

  it("offers a square Catppuccin Mocha variant with Tutora orange", () => {
    const mocha = themes.catpuccin_mocha;
    const tutoraMocha = themes.tutora_catpuccin_mocha;

    expect(tutoraMocha.background).toBe(mocha.background);
    expect(tutoraMocha.card).toBe(mocha.card);
    expect(tutoraMocha.surface).toBe(mocha.surface);
    expect(tutoraMocha.primary).toBe("23 88% 57%");
    expect(tutoraMocha.ring).toBe(tutoraMocha.primary);
    expect(tutoraMocha.enforceRadius).toBe("none");
    expect(tutoraMocha.enforceShadow).toBe("none");
  });
});
