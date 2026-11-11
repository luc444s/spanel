import { getLocale, type Locale } from "./locale";
import es from "./es";

export type TranslationValues = Record<string, string | number>;

function interpolate(template: string, values?: TranslationValues): string {
  if (!values) {
    return template;
  }
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in values ? String(values[name]) : match,
  );
}

/**
 * English is the source language: the key is the message. Spanish comes from
 * the catalog and falls back to English when a translation is missing.
 */
export function translate(
  key: string,
  values?: TranslationValues,
  locale: Locale = getLocale(),
): string {
  const template = locale === "es" ? es[key] ?? key : key;
  return interpolate(template, values);
}

export function t(key: string, values?: TranslationValues): string {
  return translate(key, values);
}
