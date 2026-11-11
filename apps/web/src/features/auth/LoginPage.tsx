import { useMutation } from "@tanstack/react-query";
import { FormEvent, useEffect, useRef, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { APP_NAME } from "../../brand";

import { login } from "./api";
import { useAuthStore } from "./store";
import { ApiError } from "@systutor/shell/api/client";
import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@systutor/shell/ui/card";
import { Input } from "@systutor/shell/ui/input";
import { GithubLink } from "../../shared/layout/GithubLink";
import { useT } from "../../i18n/use-i18n";

type LocationState = {
  from?: {
    pathname?: string;
  };
};

export function LoginPage() {
  const t = useT();
  const token = useAuthStore((state) => state.token);
  const setSession = useAuthStore((state) => state.setSession);
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const defaultRedirect = "/app/mail";
  const from = (location.state as LocationState | null)?.from?.pathname ?? defaultRedirect;

  const loggedInRef = useRef(false);

  const loginMutation = useMutation({
    mutationFn: login,
    onSuccess: (response) => {
      setSession(response.access_token);
      loggedInRef.current = true;
      if (response.user.category === "driver") {
        navigate("/app/logistics/vehicle-sessions", { replace: true });
      } else {
        navigate(from, { replace: true });
      }
    },
  });

  useEffect(() => {
    if (token && !loggedInRef.current) {
      navigate(defaultRedirect, { replace: true });
    }
  }, [navigate, token]);

  if (token && !loggedInRef.current) {
    return <Navigate replace to={defaultRedirect} />;
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    loginMutation.mutate({ email, password });
  }

  const errorMessage =
    loginMutation.error instanceof ApiError
      ? loginMutation.error.message
      : loginMutation.error
        ? t("Unable to sign in.")
        : null;

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-8">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>{APP_NAME}</CardTitle>
          <CardDescription>{t("The Docker-native cPanel alternative for agencies and self-hosters.")}</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={handleSubmit}>
            {errorMessage ? <Alert title={t("Login failed")}>{errorMessage}</Alert> : null}

            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground" htmlFor="email">
                {t("Email")}
              </label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground" htmlFor="password">
                {t("Password")}
              </label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>

            <Button className="w-full" disabled={loginMutation.isPending} type="submit">
              {loginMutation.isPending ? t("Signing in...") : t("Sign in")}
            </Button>
          </form>
        </CardContent>
      </Card>
      <GithubLink />
    </main>
  );
}
