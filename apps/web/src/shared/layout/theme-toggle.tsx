import { useThemeStore } from "@systutor/themes";
import { THEME_NAMES } from "@systutor/themes";

const THEME_LABELS: Record<string, string> = {
  dark: "Oscuro",
  light: "Claro",
  retro: "Retro (SAP)",
  bodega_express: "Bodega Express",
  catpuccin_mocha: "Catppuccin Mocha",
  nord: "Nord",
  nord_dark: "Nord Dark",
};

export function ThemeToggle() {
  const theme = useThemeStore((s) => s.theme);
  const setTheme = useThemeStore((s) => s.setTheme);

  return (
    <label className="flex items-center gap-2 text-xs text-muted-foreground">
      <span>Tema</span>
      <select
        value={theme}
        onChange={(e) => setTheme(e.target.value as typeof theme)}
        className="rounded-md border border-border bg-card px-2 py-1 text-xs text-foreground"
      >
        {THEME_NAMES.map((value) => (
          <option key={value} value={value}>
            {THEME_LABELS[value] ?? value}
          </option>
        ))}
      </select>
    </label>
  );
}
