import { setLocale, useLocale, useT } from "../../i18n/use-i18n";

export function LanguageToggle() {
  const locale = useLocale();
  const t = useT();

  return (
    <label className="flex items-center gap-2 text-xs text-muted-foreground">
      <span>{t("Language")}</span>
      <select
        value={locale}
        onChange={(event) => setLocale(event.target.value === "es" ? "es" : "en")}
        className="rounded-md border border-border bg-card px-2 py-1 text-xs text-foreground"
      >
        <option value="en">{t("English")}</option>
        <option value="es">{t("Spanish")}</option>
      </select>
    </label>
  );
}
