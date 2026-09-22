export type ThemeName = "light" | "dark" | "retro" | "catpuccin_mocha" | "nord" | "nord_dark";

export type ThemeTokens = {
  // ----- Palette (26 vars) -----
  colorScheme: string;
  background: string;
  foreground: string;
  card: string;
  cardForeground: string;
  popover: string;
  popoverForeground: string;
  primary: string;
  primaryForeground: string;
  secondary: string;
  secondaryForeground: string;
  muted: string;
  mutedForeground: string;
  accent: string;
  accentForeground: string;
  destructive: string;
  destructiveForeground: string;
  success: string;
  warning: string;
  border: string;
  input: string;
  ring: string;
  sidebar: string;
  sidebarForeground: string;
  sidebarMuted: string;
  surface: string;
  surfaceAlt: string;
  radius: string;
  cardShadow: string;

  // ----- Typography -----
  fontFamily: string; // "" = no override (app default)
  fontSmoothing: "antialiased" | "none" | "inherit";
  baseFontSize: string; // "" = no override
  moduleTitleTransform: "uppercase" | "none";
  moduleTitleSize: string; // "" = no override
  moduleTitleWeight: string; // "" = no override
  moduleTitlePadding: string; // "" = no override

  // ----- Sidebar links -----
  sidebarItemPadding: string; // "" = no override
  sidebarIndent: string; // "" = no override (border-left width)
  sidebarLinkSize: string; // "" = no override
  sidebarLinkWeight: string; // "" = no override

  // ----- Spacing / separations ("" = no override, Tailwind default) -----
  spaceY: string;
  spaceX: string;
  cardPadding: string;
  sectionPadding: string;

  // ----- Radius / shadow -----
  enforceRadius: "full" | "none";
  enforceShadow: "full" | "none";

  // ----- Buttons -----
  buttonRadius: string; // "" = no override
  buttonPadding: string; // "" = no override

  // ----- Tables -----
  tableFontSize: string; // "" = no override
  tableCellPadding: string; // "" = no override
  tableBorder: "bordered" | "none";
  tableBorderCollapse: "collapse" | "separate";
  tableHeaderBg: "primary" | "themed" | "none";
  tableZebra: "surface-alt" | "none";

  // ----- Header bar -----
  headerStyle: "primary" | "themed";

  // ----- Sidebar active item -----
  sidebarActiveStyle: "primary" | "themed";
};

export const NO_OVERRIDE = {
  fontFamily: "",
  fontSmoothing: "inherit" as const,
  baseFontSize: "",
  moduleTitleTransform: "none" as const,
  moduleTitleSize: "",
  moduleTitleWeight: "",
  moduleTitlePadding: "",
  sidebarItemPadding: "",
  sidebarIndent: "",
  sidebarLinkSize: "",
  sidebarLinkWeight: "",
  spaceY: "",
  spaceX: "",
  cardPadding: "",
  sectionPadding: "",
  enforceRadius: "full" as const,
  enforceShadow: "full" as const,
  buttonRadius: "",
  buttonPadding: "",
  tableFontSize: "",
  tableCellPadding: "",
  tableBorder: "none" as const,
  tableBorderCollapse: "separate" as const,
  tableHeaderBg: "none" as const,
  tableZebra: "none" as const,
  headerStyle: "themed" as const,
  sidebarActiveStyle: "themed" as const,
};
