import { lazy, Suspense } from "react"
import { Navigate, Route, Routes } from "react-router-dom"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { AppShell } from "@/components/app-shell"
import { LoginPage } from "@/pages/login"
import { Skeleton } from "@/components/ui/skeleton"
import { api, ApiError } from "@/lib/api"

const OverviewPage = lazy(() => import("@/pages/overview").then((module) => ({ default: module.OverviewPage })))
const LivePage = lazy(() => import("@/pages/live").then((module) => ({ default: module.LivePage })))
const HistoryPage = lazy(() => import("@/pages/history").then((module) => ({ default: module.HistoryPage })))
const TestConsolePage = lazy(() => import("@/pages/test-console").then((module) => ({ default: module.TestConsolePage })))
const SettingsPage = lazy(() => import("@/pages/settings").then((module) => ({ default: module.SettingsPage })))
const AccessPage = lazy(() => import("@/pages/access").then((module) => ({ default: module.AccessPage })))
const GuidePage = lazy(() => import("@/pages/guide").then((module) => ({ default: module.GuidePage })))

export default function App() {
  const queryClient = useQueryClient()
  const session = useQuery({ queryKey: ["auth", "me"], queryFn: api.me, retry: false, staleTime: 30_000 })
  if (session.isPending) return <div className="auth-loading" aria-label="Loading session"><Skeleton className="h-9 w-40" /><Skeleton className="h-4 w-64" /></div>
  if (session.isError) {
    const unauthorized = session.error instanceof ApiError && session.error.status === 401
    return <LoginPage unavailable={!unauthorized} onAuthenticated={(user) => queryClient.setQueryData(["auth", "me"], { user })} />
  }
  const user = session.data.user
  return <AppShell user={user}><Suspense fallback={<div className="page"><Skeleton className="h-9 w-48" /><Skeleton className="mt-5 h-[520px]" /></div>}><Routes>
    <Route path="/" element={<OverviewPage />} />
    <Route path="/live" element={<LivePage />} />
    <Route path="/history" element={<HistoryPage />} />
    <Route path="/test" element={<TestConsolePage />} />
    <Route path="/guide" element={<GuidePage />} />
    <Route path="/settings" element={<Navigate to="/settings/providers" replace />} />
    <Route path="/settings/providers" element={<SettingsPage section="providers" />} />
    <Route path="/settings/routing" element={<SettingsPage section="routing" />} />
    <Route path="/settings/pricing" element={<SettingsPage section="pricing" />} />
    <Route path="/settings/storage" element={<SettingsPage section="storage" />} />
    <Route path="/settings/alerts" element={<SettingsPage section="alerts" />} />
    <Route path="/settings/access" element={user.role === "admin" ? <AccessPage /> : <Navigate to="/" replace />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></Suspense></AppShell>
}
