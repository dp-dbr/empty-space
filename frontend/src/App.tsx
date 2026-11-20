import { useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api } from "@/api/client";
import { Loading } from "@/components/ErrorBox";
import { NativeSelect } from "@/components/ui/input";
import { LOCALES } from "@/i18n";
import { hasRole, useMe } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { AdminPage } from "@/pages/AdminPage";
import { ConnectionsPage } from "@/pages/ConnectionsPage";
import { CycleDetailPage, CyclesPage } from "@/pages/CyclesPage";
import { JobPage } from "@/pages/JobPage";
import { JobsPage } from "@/pages/JobsPage";
import { LoginPage } from "@/pages/LoginPage";
import { OAuthConsentPage } from "@/pages/OAuthConsentPage";

const SubjectPage = lazy(() => import("@/pages/SubjectPage"));

function Shell({ children }: { children: React.ReactNode }) {
  const { t, i18n } = useTranslation();
  const { data: me } = useMe();
  const qc = useQueryClient();
  const links = [
    { to: "/jobs", label: t("nav.jobs"), show: true },
    { to: "/connections", label: t("nav.connections"), show: true },
    { to: "/cycles", label: t("nav.cycles"), show: hasRole(me, "admin", "cycle_owner", "viewer") },
    { to: "/admin", label: t("nav.admin"), show: hasRole(me, "admin") },
  ];
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:bg-white focus:p-2">
        {t("app.skip")}
      </a>
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-4 px-4 py-3">
          <Link to="/jobs" className="font-semibold">
            {t("app.name")}
          </Link>
          <nav aria-label="Main" className="flex flex-wrap gap-1">
            {links
              .filter((l) => l.show)
              .map((l) => (
                <NavLink
                  key={l.to}
                  to={l.to}
                  className={({ isActive }) =>
                    cn("rounded-md px-3 py-1.5 text-sm hover:bg-slate-100", isActive && "bg-slate-100 font-medium")
                  }
                >
                  {l.label}
                </NavLink>
              ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <label className="flex items-center gap-1">
              <span className="sr-only">{t("app.language")}</span>
              <NativeSelect
                aria-label={t("app.language")}
                value={i18n.language.slice(0, 2)}
                onChange={(e) => {
                  i18n.changeLanguage(e.target.value);
                  try {
                    localStorage.setItem("dpac.locale", e.target.value);
                  } catch {
                    /* per-viewer convenience only */
                  }
                }}
              >
                {LOCALES.map((l) => (
                  <option key={l} value={l}>
                    {l.toUpperCase()}
                  </option>
                ))}
              </NativeSelect>
            </label>
            {me && <span data-testid="me">{me.user.name}</span>}
            <button
              className="text-slate-600 underline-offset-4 hover:underline"
              onClick={async () => {
                await api.POST("/api/v1/auth/logout");
                qc.clear();
                window.location.href = "/login";
              }}
            >
              {t("app.logout")}
            </button>
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto max-w-7xl px-4 py-6">
        {children}
      </main>
    </div>
  );
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { data, isLoading, error } = useMe();
  const loc = useLocation();
  if (isLoading) return <Loading />;
  if (error || !data) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  return <Shell>{children}</Shell>;
}

export function App() {
  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/oauth/consent" element={<RequireAuth><OAuthConsentPage /></RequireAuth>} />
        <Route path="/jobs" element={<RequireAuth><JobsPage /></RequireAuth>} />
        <Route path="/jobs/:jobId" element={<RequireAuth><JobPage /></RequireAuth>} />
        <Route path="/connections" element={<RequireAuth><ConnectionsPage /></RequireAuth>} />
        <Route path="/cycles" element={<RequireAuth><CyclesPage /></RequireAuth>} />
        <Route path="/cycles/:cycleId" element={<RequireAuth><CycleDetailPage /></RequireAuth>} />
        <Route path="/subjects/:subjectId" element={<RequireAuth><SubjectPage /></RequireAuth>} />
        <Route path="/admin" element={<RequireAuth><AdminPage /></RequireAuth>} />
        <Route path="*" element={<Navigate to="/jobs" replace />} />
      </Routes>
    </Suspense>
  );
}
