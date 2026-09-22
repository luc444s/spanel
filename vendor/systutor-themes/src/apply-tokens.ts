import { themes, type ThemeName, type ThemeTokens } from "./tokens";

const PALETTE_KEYS = [
  "colorScheme",
  "background",
  "foreground",
  "card",
  "cardForeground",
  "popover",
  "popoverForeground",
  "primary",
  "primaryForeground",
  "secondary",
  "secondaryForeground",
  "muted",
  "mutedForeground",
  "accent",
  "accentForeground",
  "destructive",
  "destructiveForeground",
  "success",
  "warning",
  "border",
  "input",
  "ring",
  "sidebar",
  "sidebarForeground",
  "sidebarMuted",
  "surface",
  "surfaceAlt",
  "radius",
  "cardShadow",
] as const;

function kebab(key: string): string {
  return key.replace(/[A-Z]/g, (match) => "-" + match.toLowerCase());
}

function themeSelector(name: ThemeName): string {
  return name === "light" ? ":root" : `.${name}`;
}

function paletteVars(t: ThemeTokens): string {
  return PALETTE_KEYS.map((key) => `  --${kebab(key)}: ${t[key]};`).join("\n");
}

function overrideRules(name: ThemeName, t: ThemeTokens): string {
  const s = themeSelector(name);
  const htmlSel = name === "light" ? "html" : `html.${name}`;
  const rules: string[] = [];

  if (t.fontFamily) {
    rules.push(`${s}, ${s} * { font-family: ${t.fontFamily} !important; }`);
  }
  if (t.fontSmoothing === "none") {
    rules.push(
      `${s} * { -webkit-font-smoothing: none !important; font-smooth: never !important; text-rendering: optimizeSpeed !important; }`,
    );
  }
  if (t.baseFontSize) {
    rules.push(`${htmlSel} { font-size: ${t.baseFontSize}; }`);
  }

  const moduleTitle: string[] = [];
  if (t.moduleTitlePadding) moduleTitle.push(`padding: ${t.moduleTitlePadding} !important`);
  if (t.moduleTitleSize) moduleTitle.push(`font-size: ${t.moduleTitleSize} !important`);
  if (t.moduleTitleWeight) moduleTitle.push(`font-weight: ${t.moduleTitleWeight} !important`);
  if (t.moduleTitleTransform !== "none")
    moduleTitle.push(`text-transform: ${t.moduleTitleTransform} !important`);
  if (moduleTitle.length) {
    rules.push(`${s} aside nav button { ${moduleTitle.join("; ")}; }`);
  }

  const sidebarLink: string[] = [];
  if (t.sidebarItemPadding) sidebarLink.push(`padding: ${t.sidebarItemPadding} !important`);
  if (t.sidebarIndent)
    sidebarLink.push(`border-left: ${t.sidebarIndent} solid hsl(var(--border)) !important`);
  if (t.sidebarLinkSize) sidebarLink.push(`font-size: ${t.sidebarLinkSize} !important`);
  if (t.sidebarLinkWeight) sidebarLink.push(`font-weight: ${t.sidebarLinkWeight} !important`);
  if (sidebarLink.length) {
    rules.push(`${s} aside nav a { ${sidebarLink.join("; ")}; }`);
  }

  rules.push(`${s} aside { background: hsl(var(--sidebar)); }`);

  if (t.spaceY) {
    rules.push(
      `${s} [class*="space-y"] > :not([hidden]) ~ :not([hidden]) { margin-top: ${t.spaceY} !important; }`,
    );
  }
  if (t.spaceX) {
    rules.push(
      `${s} [class*="space-x"] > :not([hidden]) ~ :not([hidden]) { margin-left: ${t.spaceX} !important; }`,
    );
  }
  if (t.cardPadding) {
    rules.push(`${s} .card { padding: ${t.cardPadding} !important; }`);
  }
  if (t.sectionPadding) {
    rules.push(
      `${s} [class*="p-6"], ${s} [class*="p-5"], ${s} [class*="p-4"] { padding: ${t.sectionPadding} !important; }`,
    );
  }

  if (t.enforceRadius === "none") {
    rules.push(`${s} * { border-radius: 0 !important; }`);
  }
  if (t.enforceShadow === "none") {
    rules.push(`${s} * { box-shadow: none !important; }`);
  }

  if (t.buttonRadius) {
    rules.push(`${s} button { border-radius: ${t.buttonRadius} !important; }`);
  }
  if (t.buttonPadding) {
    rules.push(`${s} button { padding: ${t.buttonPadding} !important; }`);
  }

  const table: string[] = [];
  if (t.tableBorderCollapse === "collapse") table.push("border-collapse: collapse");
  if (t.tableFontSize) table.push(`font-size: ${t.tableFontSize} !important`);
  if (table.length) {
    table.push("margin: 0 !important");
    rules.push(`${s} table { ${table.join("; ")}; }`);
  }
  if (t.tableBorder === "bordered") {
    rules.push(`${s} th, ${s} td { border: 1px solid hsl(var(--border)) !important; }`);
  }
  if (t.tableCellPadding) {
    rules.push(`${s} th, ${s} td { padding: ${t.tableCellPadding} !important; }`);
  }
  if (t.tableHeaderBg === "primary") {
    rules.push(
      `${s} th { background: hsl(var(--primary)) !important; color: hsl(var(--primary-foreground)) !important; text-align: left; }`,
    );
  } else if (t.tableHeaderBg === "themed") {
    rules.push(`${s} th { background: hsl(var(--surface-alt)) !important; text-align: left; }`);
  }
  if (t.tableZebra === "surface-alt") {
    rules.push(
      `${s} tbody tr:nth-child(even) { background: hsl(var(--surface-alt)) !important; }`,
    );
  }

  if (t.headerStyle === "primary") {
    rules.push(
      `${s} header { background: hsl(var(--primary)) !important; color: hsl(var(--primary-foreground)) !important; border-bottom: 1px solid hsl(var(--border)); }`,
    );
    rules.push(
      `${s} header .text-foreground, ${s} header .text-muted-foreground { color: hsl(var(--primary-foreground)); }`,
    );
  }

  if (t.sidebarActiveStyle === "primary") {
    rules.push(
      `${s} aside nav a.bg-accent { background: hsl(var(--primary)) !important; color: hsl(var(--primary-foreground)) !important; }`,
    );
    rules.push(`${s} aside nav a:hover { border-left-color: hsl(var(--primary)); }`);
  }

  return rules.join("\n");
}

export function buildThemeStylesheet(): string {
  const blocks = (Object.keys(themes) as ThemeName[]).map((name) => {
    const t = themes[name];
    const sel = themeSelector(name);
    const vars = paletteVars(t);
    const overrides = overrideRules(name, t);
    let css = `${sel} {\n${vars}\n}`;
    if (overrides) css += `\n\n${overrides}`;
    return css;
  });
  return blocks.join("\n\n");
}

const STYLE_ID = "systutor-theme-tokens";

export function injectThemeTokens(): void {
  if (typeof document === "undefined") return;
  let style = document.getElementById(STYLE_ID) as HTMLStyleElement | null;
  if (!style) {
    style = document.createElement("style");
    style.id = STYLE_ID;
    document.head.appendChild(style);
  }
  style.textContent = buildThemeStylesheet();
}
