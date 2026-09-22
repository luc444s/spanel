import { useState } from "react";
import { apiRequest } from "../api/client";
import type { LoginResponse, UserProfile } from "../api/types";
import { setToken } from "../auth/token";
import { Alert } from "../ui/alert";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Input } from "../ui/input";

type LoginProps = {
  onLogin: (user: UserProfile) => void;
  title?: string;
  description?: string;
};

export function Login({ onLogin, title = "SYSTUTOR", description = "Iniciar sesión" }: LoginProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await apiRequest<LoginResponse>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setToken(res.access_token);
      onLogin(res.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen flex items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-6" onSubmit={handleSubmit}>
            {error && <Alert title="Error">{error}</Alert>}
            <label className="block space-y-2 text-sm text-foreground">
              <span>Email</span>
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
            </label>
            <label className="block space-y-2 text-sm text-foreground">
              <span>Password</span>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </label>
            <div className="flex justify-end">
              <Button type="submit" disabled={loading}>
                {loading ? "Entrando…" : "Entrar"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
