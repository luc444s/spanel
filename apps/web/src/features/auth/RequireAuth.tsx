import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuthStore } from "./store";

export function shouldRedirectToLogin(token: string | null) {
  return !token;
}

export function RequireAuth() {
  const token = useAuthStore((state) => state.token);
  const location = useLocation();

  if (shouldRedirectToLogin(token)) {
    return <Navigate replace to="/login" state={{ from: location }} />;
  }

  return <Outlet />;
}
