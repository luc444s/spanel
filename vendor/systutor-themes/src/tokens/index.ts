import type { ThemeName, ThemeTokens } from "./types";
import { light } from "./light";
import { dark } from "./dark";
import { retro } from "./retro";
import { catpuccin_mocha } from "./catpuccin_mocha";
import { nord } from "./nord";
import { nord_dark } from "./nord_dark";

export type { ThemeName, ThemeTokens } from "./types";

export const themes: Record<ThemeName, ThemeTokens> = {
  light,
  dark,
  retro,
  catpuccin_mocha,
  nord,
  nord_dark,
};

export const THEME_NAMES: ThemeName[] = ["dark", "light", "retro", "catpuccin_mocha", "nord", "nord_dark"];
