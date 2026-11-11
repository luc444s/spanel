import { createContext, useContext, useSyncExternalStore, type ReactNode } from "react";

import { getLocale, subscribeLocale, type Locale } from "./locale";
import { translate, type TranslationValues } from "./index";

const LocaleContext = createContext<Locale>(getLocale());

export function LocaleProvider({ children }: { children: ReactNode }) {
  const locale = useSyncExternalStore(subscribeLocale, getLocale, getLocale);
  return <LocaleContext.Provider value={locale}>{children}</LocaleContext.Provider>;
}

export function useLocale(): Locale {
  return useContext(LocaleContext);
}

export function useT() {
  const locale = useLocale();
  return (key: string, values?: TranslationValues) => translate(key, values, locale);
}

export { getLocale, setLocale, subscribeLocale } from "./locale";
export type { Locale } from "./locale";
export { translate, t } from "./index";
export type { TranslationValues } from "./index";
