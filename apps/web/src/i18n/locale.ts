export type Locale = "en" | "es";

const LOCALE_KEY = "spanel.locale";
const DEFAULT_LOCALE: Locale = "en";

function isLocale(value: string | null): value is Locale {
  return value === "en" || value === "es";
}

function readStoredLocale(): Locale {
  if (typeof window === "undefined") {
    return DEFAULT_LOCALE;
  }
  const stored = window.localStorage.getItem(LOCALE_KEY);
  return isLocale(stored) ? stored : DEFAULT_LOCALE;
}

let currentLocale: Locale = readStoredLocale();
const listeners = new Set<() => void>();

if (typeof document !== "undefined") {
  document.documentElement.lang = currentLocale;
}

export function getLocale(): Locale {
  return currentLocale;
}

export function setLocale(locale: Locale): void {
  if (locale === currentLocale) {
    return;
  }
  currentLocale = locale;
  if (typeof window !== "undefined") {
    window.localStorage.setItem(LOCALE_KEY, locale);
  }
  if (typeof document !== "undefined") {
    document.documentElement.lang = locale;
  }
  for (const listener of listeners) {
    listener();
  }
}

export function subscribeLocale(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
