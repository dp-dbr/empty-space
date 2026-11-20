import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, unwrap } from "@/api/client";
import { ErrorBox } from "@/components/ErrorBox";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function LoginPage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [tenant, setTenant] = useState("default");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      unwrap(await api.POST("/api/v1/auth/login", { body: { tenant, email, password } }));
      await qc.invalidateQueries();
      const next = params.get("next");
      if (next?.startsWith("/oauth/")) window.location.href = next;
      else nav(next && next.startsWith("/") ? next : "/jobs");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <h1 className="text-xl font-semibold">{t("app.name")}</h1>
          <CardTitle className="text-slate-600">{t("login.title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-3" onSubmit={submit}>
            <div className="flex flex-col gap-1">
              <Label htmlFor="tenant">{t("login.tenant")}</Label>
              <Input id="tenant" value={tenant} onChange={(e) => setTenant(e.target.value)} required />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="email">{t("login.email")}</Label>
              <Input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="password">{t("login.password")}</Label>
              <Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </div>
            {error ? <ErrorBox error={error} /> : null}
            <Button type="submit" disabled={busy}>
              {t("login.submit")}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
