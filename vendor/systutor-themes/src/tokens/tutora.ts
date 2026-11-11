import { NO_OVERRIDE, type ThemeTokens } from "./types";

/**
 * Tutora Business theme.
 * Brand orange sampled from the logo: #F27A31 (hsl(23 88% 57%)).
 * Dark tech background with blue-graphite surfaces.
 */
export const tutora: ThemeTokens = {
  colorScheme: "dark",
  background: "220 20% 5%",
  foreground: "210 20% 93%",
  card: "220 16% 8%",
  cardForeground: "210 20% 93%",
  popover: "220 16% 8%",
  popoverForeground: "210 20% 93%",
  primary: "23 88% 57%",
  primaryForeground: "220 25% 7%",
  secondary: "220 14% 11%",
  secondaryForeground: "210 20% 93%",
  muted: "220 14% 11%",
  mutedForeground: "215 14% 62%",
  accent: "23 88% 57%",
  accentForeground: "220 25% 7%",
  destructive: "0 63% 45%",
  destructiveForeground: "0 0% 98%",
  success: "142 69% 50%",
  warning: "38 92% 58%",
  border: "220 14% 16%",
  input: "220 14% 22%",
  ring: "23 88% 57%",
  sidebar: "220 20% 6%",
  sidebarForeground: "210 20% 93%",
  sidebarMuted: "215 14% 62%",
  chrome: "220 20% 5%",
  chromeForeground: "210 20% 93%",
  chromeMuted: "215 14% 62%",
  surface: "220 16% 8%",
  surfaceAlt: "220 14% 11%",
  radius: "3px",
  cardShadow: "0 24px 48px -24px rgb(0 0 0 / 0.65)",
  ...NO_OVERRIDE,
};
