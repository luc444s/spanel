import { RouterProvider } from "react-router-dom";

import { LocaleProvider } from "../i18n/use-i18n";
import "../i18n/api-errors";
import { AppProviders } from "./providers";
import { appRouter } from "./router";

export function App() {
  return (
    <LocaleProvider>
      <AppProviders>
        <RouterProvider router={appRouter} />
      </AppProviders>
    </LocaleProvider>
  );
}
