import { IconArrowRight, IconLock, IconMoon, IconSun } from "@tabler/icons-react"
import { useMutation } from "@tanstack/react-query"
import { useState, type FormEvent } from "react"
import { api } from "@/lib/api"
import type { AuthUser } from "@/lib/types"
import { useTheme } from "@/components/theme-provider"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { BrandLogo } from "@/components/brand-logo"

export function LoginPage({ onAuthenticated, unavailable = false }: { onAuthenticated: (user: AuthUser) => void; unavailable?: boolean }) {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const { theme, setTheme } = useTheme()
  const login = useMutation({ mutationFn: api.login, onSuccess: ({ user }) => onAuthenticated(user) })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (email.trim() && password) login.mutate({ email: email.trim(), password })
  }
  return <main className="login-page">
    <header className="login-topbar">
      <div className="login-brand"><BrandLogo /><span>PRISMUX</span></div>
      <Button variant="outline" size="icon" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`} onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
        {theme === "dark" ? <IconSun size={17} /> : <IconMoon size={17} />}
      </Button>
    </header>
    <section className="login-stage">
      <div className="login-context">
        <div className="login-kicker"><span className="semantic-dot success" />Self-hosted AI gateway</div>
        <h1>One API. Every model. Full control.</h1>
        <p>Securely connect providers, route model traffic, enforce capacity, track cost, and inspect every request from one protected control plane.</p>
        <div className="login-trust"><IconLock size={16} /><span>Supabase Auth · HttpOnly sessions · role-enforced actions</span></div>
      </div>
      <form className="login-panel" onSubmit={submit}>
        <div>
          <span className="login-step">Operator access</span>
          <h2>Sign in</h2>
          <p>Use the account created or invited by your administrator.</p>
        </div>
        <label>Email<Input type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="operator@example.com" required /></label>
        <label>Password<Input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Enter your password" required /></label>
        {(login.isError || unavailable) && <div className="login-error" role="alert">{unavailable ? "The authentication service is unavailable. Check the deployment configuration." : login.error?.message}</div>}
        <Button type="submit" size="lg" disabled={login.isPending || unavailable}>{login.isPending ? "Verifying account…" : <>Continue <IconArrowRight size={17} /></>}</Button>
        <p className="login-footnote">Public registration is disabled. Access is managed by an Admin.</p>
      </form>
    </section>
  </main>
}
