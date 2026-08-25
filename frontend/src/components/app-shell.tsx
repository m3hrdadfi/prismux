import * as DialogPrimitive from "@radix-ui/react-dialog"
import { IconActivityHeartbeat, IconArrowsShuffle, IconBook2, IconChartHistogram, IconChevronDown, IconChevronLeft, IconChevronRight, IconCoin, IconDatabase, IconDots, IconFlask, IconLayoutDashboard, IconLogout, IconMenu2, IconMoon, IconRefresh, IconServer, IconSettings, IconShieldCheck, IconSun, IconTrash, IconUsers, IconX } from "@tabler/icons-react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState, type ReactNode } from "react"
import { NavLink, useLocation } from "react-router-dom"
import { toast } from "sonner"
import { api } from "@/lib/api"
import type { AuthUser } from "@/lib/types"
import { cn, formatUptime } from "@/lib/utils"
import { useTheme } from "@/components/theme-provider"
import { BrandLogo } from "@/components/brand-logo"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogTitle, AlertDialogTrigger, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"

const nav = [
  { to: "/", label: "Overview", icon: IconLayoutDashboard },
  { to: "/live", label: "Live Feed", icon: IconActivityHeartbeat },
  { to: "/history", label: "History", icon: IconChartHistogram },
  { to: "/test", label: "Test Console", icon: IconFlask },
  { to: "/guide", label: "Guide", icon: IconBook2 },
]

const settingsNav = [
  { to: "/settings/providers", label: "Providers", icon: IconServer },
  { to: "/settings/routing", label: "Model routing", icon: IconArrowsShuffle },
  { to: "/settings/pricing", label: "Pricing", icon: IconCoin },
  { to: "/settings/storage", label: "Storage", icon: IconDatabase },
  { to: "/settings/alerts", label: "Alerts", icon: IconShieldCheck },
  { to: "/settings/access", label: "Access", icon: IconUsers, adminOnly: true },
]

const pageCopy: Record<string, { title: string; subtitle: string }> = {
  "/": { title: "Overview", subtitle: "Capacity, traffic, and upstream health" },
  "/live": { title: "Live Feed", subtitle: "Incoming proxy activity" },
  "/history": { title: "History", subtitle: "Search and manage request records" },
  "/test": { title: "Test Console", subtitle: "Validate the active provider path" },
  "/guide": { title: "Guide", subtitle: "Connect clients and call providers" },
  "/settings/providers": { title: "Providers", subtitle: "Connections, credentials, models, and capacity" },
  "/settings/routing": { title: "Model routing", subtitle: "Aliases, primary targets, and ordered fallbacks" },
  "/settings/pricing": { title: "Pricing", subtitle: "Per-provider model costs" },
  "/settings/storage": { title: "Storage", subtitle: "Retention and persistence controls" },
  "/settings/alerts": { title: "Alerts", subtitle: "Operational warning thresholds" },
  "/settings/access": { title: "Access", subtitle: "Users, roles, machine keys, and security activity" },
}

function Navigation({ user, collapsed = false, onNavigate }: { user: AuthUser; collapsed?: boolean; onNavigate?: () => void }) {
  const location = useLocation()
  const settingsActive = location.pathname.startsWith("/settings")
  const [settingsOpen, setSettingsOpen] = useState(() => settingsActive || (localStorage.getItem("prismux-settings-nav") ?? localStorage.getItem("rate-limit-proxy-settings-nav")) !== "collapsed")
  useEffect(() => { if (settingsActive) setSettingsOpen(true) }, [settingsActive])
  useEffect(() => localStorage.setItem("prismux-settings-nav", settingsOpen ? "expanded" : "collapsed"), [settingsOpen])
  return <>
    <NavLink to="/" className="brand" onClick={onNavigate}>
      <BrandLogo />
      <span className="brand-copy">PRISMUX<small>AI control plane</small></span>
    </NavLink>
    <nav className="nav-list" aria-label="Dashboard">
      {nav.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} end={to === "/"} onClick={onNavigate} className={({ isActive }) => cn("nav-link", isActive && "active")} title={collapsed ? label : undefined}>
        <Icon size={18} stroke={1.7} /><span className="nav-label">{label}</span>
      </NavLink>)}
      <div className={cn("nav-settings-group", settingsActive && "active", settingsOpen && "open")}>
        <div className="nav-settings-parent">
          <NavLink to="/settings/providers" onClick={onNavigate} className={cn("nav-link", settingsActive && "active")} title={collapsed ? "Settings" : undefined}>
            <IconSettings size={18} stroke={1.7} /><span className="nav-label">Settings</span>
          </NavLink>
          <button type="button" className="settings-expand" aria-label={settingsOpen ? "Collapse settings navigation" : "Expand settings navigation"} aria-expanded={settingsOpen} onClick={() => setSettingsOpen((value) => !value)}>
            <IconChevronDown size={15} />
          </button>
        </div>
        <div className="settings-subnav" aria-label="Settings">
          {settingsNav.filter((item) => !item.adminOnly || user.role === "admin").map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} onClick={onNavigate} className={({ isActive }) => cn("nav-link settings-child", isActive && "active")} title={collapsed ? label : undefined}>
            <Icon size={16} stroke={1.7} /><span className="nav-label">{label}</span>
          </NavLink>)}
        </div>
      </div>
    </nav>
  </>
}

function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const nextTheme = theme === "dark" ? "light" : "dark"
  return <Button variant="outline" size="icon" aria-label={`Switch to ${nextTheme} mode`} title={`Switch to ${nextTheme} mode`} onClick={() => setTheme(nextTheme)}>
    {theme === "dark" ? <IconSun size={17} /> : <IconMoon size={17} />}
  </Button>
}

export function AppShell({ children, user }: { children: ReactNode; user: AuthUser }) {
  const [collapsed, setCollapsed] = useState(() => (localStorage.getItem("prismux-sidebar") ?? localStorage.getItem("rate-limit-proxy-sidebar")) === "collapsed")
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()
  const queryClient = useQueryClient()
  const page = pageCopy[location.pathname] || pageCopy["/"]
  const stats = useQuery({ queryKey: ["stats", "shell"], queryFn: api.stats, refetchInterval: 5000 })
  const reset = useMutation({ mutationFn: api.reset, onSuccess: async () => { await queryClient.invalidateQueries(); toast.success("Dashboard statistics reset") }, onError: (error) => toast.error(error.message) })
  const logout = useMutation({ mutationFn: api.logout, onSuccess: () => { queryClient.clear(); window.location.assign("/") }, onError: (error) => toast.error(error.message) })
  const providerCount = stats.data?.providers?.length || 0
  const healthyCount = stats.data?.providers?.filter((provider) => provider.enabled && provider.health === "healthy").length || 0
  const hasProviderFailure = Boolean(stats.data?.providers?.some((provider) => provider.enabled && ["offline", "auth_error"].includes(provider.health)))
  const hasProviderConcern = Boolean(stats.data?.providers?.some((provider) => provider.enabled && provider.health !== "healthy"))
  useEffect(() => localStorage.setItem("prismux-sidebar", collapsed ? "collapsed" : "expanded"), [collapsed])

  return <div className="app-shell">
    <aside className={cn("sidebar", collapsed && "collapsed")}>
      <div className="sidebar-inner">
        <Navigation user={user} collapsed={collapsed} />
        <div className="sidebar-foot">
          <Button className="sidebar-collapse" variant="ghost" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            {collapsed ? <IconChevronRight size={17} /> : <IconChevronLeft size={17} />}<span className="sidebar-foot-copy">Collapse</span>
          </Button>
        </div>
      </div>
    </aside>
    <div className="main-column">
      <header className="topbar">
        <div className="topbar-actions">
          <Button variant="ghost" size="icon" className="mobile-menu" onClick={() => setMobileOpen(true)} aria-label="Open navigation"><IconMenu2 size={19} /></Button>
          <div className="topbar-title"><h1>{page.title}</h1><p>{page.subtitle}</p></div>
        </div>
        <div className="topbar-actions">
          <Badge className="provider-badge" variant={stats.isError || hasProviderFailure ? "destructive" : hasProviderConcern ? "warning" : "success"}><span className={cn("semantic-dot", stats.isError || hasProviderFailure ? "error" : hasProviderConcern ? "warning" : "success")} />{providerCount ? `${healthyCount}/${providerCount} providers healthy` : stats.data?.provider.label || (stats.isError ? "Providers unavailable" : "Checking providers")}</Badge>
          <span className="account-chip" title={user.email}><span>{user.email}</span><small>{user.role}</small></span>
          <ThemeToggle />
          <DropdownMenu><DropdownMenuTrigger asChild><Button variant="outline" size="icon" aria-label="Dashboard actions"><IconDots size={18} /></Button></DropdownMenuTrigger><DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => queryClient.invalidateQueries()}><IconRefresh size={16} />Refresh data</DropdownMenuItem>
            {user.role === "admin" && <AlertDialog><AlertDialogTrigger asChild><DropdownMenuItem onSelect={(event) => event.preventDefault()} className="text-destructive"><IconTrash size={16} />Reset statistics</DropdownMenuItem></AlertDialogTrigger><AlertDialogContent><AlertDialogTitle>Reset dashboard statistics?</AlertDialogTitle><AlertDialogDescription className="text-sm text-muted-foreground">This permanently removes request history, metrics, and queue samples. Provider settings are kept.</AlertDialogDescription><div className="flex justify-end gap-2"><AlertDialogCancel asChild><Button variant="outline">Cancel</Button></AlertDialogCancel><AlertDialogAction asChild><Button variant="destructive" onClick={() => reset.mutate()}>Reset statistics</Button></AlertDialogAction></div></AlertDialogContent></AlertDialog>}
            <DropdownMenuItem onSelect={() => logout.mutate()}><IconLogout size={16} />Sign out</DropdownMenuItem>
          </DropdownMenuContent></DropdownMenu>
        </div>
      </header>
      <main>{children}</main>
    </div>
    <DialogPrimitive.Root open={mobileOpen} onOpenChange={setMobileOpen}>
      <DialogContent className="left-0 top-0 h-[100dvh] w-[280px] max-w-[86vw] translate-x-0 translate-y-0 rounded-none border-y-0 border-l-0 p-3">
        <DialogTitle className="sr-only">Dashboard navigation</DialogTitle>
        <DialogPrimitive.Close asChild><Button variant="ghost" size="icon" className="absolute right-3 top-3" aria-label="Close navigation"><IconX size={18} /></Button></DialogPrimitive.Close>
        <Navigation user={user} onNavigate={() => setMobileOpen(false)} />
        <div className="mt-auto px-2 pb-2 text-xs text-muted-foreground">Uptime {formatUptime(stats.data?.uptime_seconds)}</div>
      </DialogContent>
    </DialogPrimitive.Root>
  </div>
}
