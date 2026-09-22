import { RouterProvider } from "react-router-dom";

import { AppProviders } from "./providers";
import { appRouter } from "./router";

export function App() {
  return (
    <AppProviders>
      <RouterProvider router={appRouter} />
    </AppProviders>
  );
}
