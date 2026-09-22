import { setTokenProvider } from "../api/client";

const TOKEN_KEY = "systutor.token";

export function getToken(): string | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
  setTokenProvider(() => token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
  setTokenProvider(() => null);
}

export function initAuth(): void {
  const token = getToken();
  setTokenProvider(() => token);
}
