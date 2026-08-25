import { IconCheck, IconCopy, IconKey, IconPlus, IconShieldCheck, IconUserPlus } from "@tabler/icons-react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState, type FormEvent } from "react"
import { toast } from "sonner"
import { api } from "@/lib/api"
import type { AccessRole, AccessUser } from "@/lib/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogTitle, AlertDialogTrigger, Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { copyText } from "@/lib/clipboard"

const roles: AccessRole[] = ["viewer", "operator", "admin"]

function UserRow({ user }: { user: AccessUser }) {
  const queryClient = useQueryClient()
  const [role, setRole] = useState<AccessRole>(user.role || "viewer")
  const update = useMutation({
    mutationFn: () => api.updateAccessUser(user.id, { email: user.email, role, disabled: user.disabled }),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["access", "users"] }); toast.success("User role updated") },
    onError: (error) => toast.error(error.message),
  })
  const toggle = useMutation({
    mutationFn: () => api.updateAccessUser(user.id, { email: user.email, role, disabled: !user.disabled }),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["access", "users"] }); toast.success(user.disabled ? "User enabled" : "User disabled") },
    onError: (error) => toast.error(error.message),
  })
  return <tr>
    <td><div className="table-primary">{user.email}</div><div className="table-secondary font-mono">{user.id}</div></td>
    <td><select className="compact-select" value={role} onChange={(event) => setRole(event.target.value as AccessRole)}>{roles.map((item) => <option key={item} value={item}>{item}</option>)}</select></td>
    <td><Badge variant={user.disabled ? "destructive" : "success"}>{user.disabled ? "Disabled" : "Active"}</Badge></td>
    <td className="table-actions"><Button size="sm" variant="outline" disabled={role === user.role || update.isPending} onClick={() => update.mutate()}>Save role</Button><Button size="sm" variant="ghost" onClick={() => toggle.mutate()} disabled={toggle.isPending}>{user.disabled ? "Enable" : "Disable"}</Button></td>
  </tr>
}

export function AccessPage() {
  const queryClient = useQueryClient()
  const users = useQuery({ queryKey: ["access", "users"], queryFn: api.accessUsers })
  const keys = useQuery({ queryKey: ["access", "keys"], queryFn: api.accessKeys })
  const audit = useQuery({ queryKey: ["access", "audit"], queryFn: api.accessAudit })
  const [email, setEmail] = useState("")
  const [role, setRole] = useState<AccessRole>("viewer")
  const [keyName, setKeyName] = useState("")
  const [expires, setExpires] = useState("")
  const [createdSecret, setCreatedSecret] = useState("")
  const [keyCopied, setKeyCopied] = useState(false)
  const createUser = useMutation({
    mutationFn: () => api.createAccessUser({ email: email.trim(), role }),
    onSuccess: async () => { setEmail(""); await queryClient.invalidateQueries({ queryKey: ["access", "users"] }); toast.success("Invitation sent") },
    onError: (error) => toast.error(error.message),
  })
  const createKey = useMutation({
    mutationFn: () => api.createAccessKey({ name: keyName.trim(), expires_at: expires ? new Date(expires).toISOString() : null }),
    onSuccess: async ({ key }) => { setKeyCopied(false); setCreatedSecret(key.secret); setKeyName(""); setExpires(""); await queryClient.invalidateQueries({ queryKey: ["access", "keys"] }) },
    onError: (error) => toast.error(error.message),
  })
  const revoke = useMutation({
    mutationFn: api.revokeAccessKey,
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["access", "keys"] }); toast.success("Machine key revoked") },
    onError: (error) => toast.error(error.message),
  })
  const submitUser = (event: FormEvent) => { event.preventDefault(); if (email.trim()) createUser.mutate() }
  const submitKey = (event: FormEvent) => { event.preventDefault(); if (keyName.trim()) createKey.mutate() }
  const copyCreatedKey = async () => {
    try {
      await copyText(createdSecret)
      setKeyCopied(true)
      toast.success("Machine key copied")
    } catch {
      setKeyCopied(false)
      toast.error("Copy failed. Select the key and copy it manually.")
    }
  }

  return <div className="page">
    <div className="page-heading"><div><h2>Access control</h2><p>Manage human roles, revocable machine credentials, and recent security activity.</p></div><Badge variant="success"><IconShieldCheck size={14} /> Supabase Auth active</Badge></div>

    <div className="access-grid">
      <section className="section access-panel">
        <div className="section-header"><div><h3>Dashboard users</h3><p>Public registration is disabled. Invite each operator explicitly.</p></div><IconUserPlus size={18} className="text-muted-foreground" /></div>
        <form className="access-create" onSubmit={submitUser}>
          <Input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="operator@example.com" aria-label="User email" required />
          <select className="compact-select" value={role} onChange={(event) => setRole(event.target.value as AccessRole)} aria-label="Initial role">{roles.map((item) => <option key={item}>{item}</option>)}</select>
          <Button type="submit" size="sm" disabled={createUser.isPending}><IconPlus size={16} />{createUser.isPending ? "Inviting" : "Invite user"}</Button>
        </form>
        <div className="table-scroll"><table className="data-table access-table access-users-table"><thead><tr><th>User</th><th>Role</th><th>Status</th><th>Action</th></tr></thead><tbody>
          {users.data?.users.map((user) => <UserRow key={user.id} user={user} />)}
          {users.isPending && <tr><td colSpan={4} className="empty-cell">Loading dashboard users…</td></tr>}
          {users.isError && <tr><td colSpan={4} className="empty-cell text-destructive">Could not load dashboard users.</td></tr>}
          {!users.isPending && !users.isError && !users.data?.users.length && <tr><td colSpan={4} className="empty-cell">No users found.</td></tr>}
        </tbody></table></div>
      </section>

      <section className="section access-panel">
        <div className="section-header"><div><h3>Machine API keys</h3><p>Keys authorize only the OpenAI-compatible proxy endpoint.</p></div><IconKey size={18} className="text-muted-foreground" /></div>
        <form className="access-create keys" onSubmit={submitKey}>
          <Input value={keyName} onChange={(event) => setKeyName(event.target.value)} placeholder="Production gateway" aria-label="Key name" required />
          <Input type="datetime-local" value={expires} onChange={(event) => setExpires(event.target.value)} aria-label="Optional expiration" />
          <Button type="submit" size="sm" disabled={createKey.isPending}><IconPlus size={16} />{createKey.isPending ? "Creating" : "Create key"}</Button>
        </form>
        <div className="table-scroll"><table className="data-table access-table access-keys-table"><thead><tr><th>Key</th><th>Scope</th><th>Last used</th><th>Action</th></tr></thead><tbody>
          {keys.data?.keys.map((key) => <tr key={key.id}><td><div className="table-primary">{key.name}</div><div className="table-secondary font-mono">prismux_live_{key.key_prefix}_••••••••</div></td><td><Badge variant="outline">proxy:invoke</Badge></td><td className="table-secondary">{key.last_used_at ? new Date(key.last_used_at).toLocaleString() : "Never"}</td><td className="table-actions">{key.revoked_at ? <Badge variant="destructive">Revoked</Badge> : <AlertDialog><AlertDialogTrigger asChild><Button size="sm" variant="ghost">Revoke</Button></AlertDialogTrigger><AlertDialogContent><AlertDialogTitle>Revoke {key.name}?</AlertDialogTitle><AlertDialogDescription className="text-sm text-muted-foreground">Requests using this key will immediately receive an authentication error.</AlertDialogDescription><div className="flex justify-end gap-2"><AlertDialogCancel asChild><Button variant="outline">Cancel</Button></AlertDialogCancel><AlertDialogAction asChild><Button variant="destructive" onClick={() => revoke.mutate(key.id)}>Revoke key</Button></AlertDialogAction></div></AlertDialogContent></AlertDialog>}</td></tr>)}
          {keys.isPending && <tr><td colSpan={4} className="empty-cell">Loading machine keys…</td></tr>}
          {keys.isError && <tr><td colSpan={4} className="empty-cell text-destructive">Could not load machine keys.</td></tr>}
          {!keys.isPending && !keys.isError && !keys.data?.keys.length && <tr><td colSpan={4} className="empty-cell">No machine keys have been created.</td></tr>}
        </tbody></table></div>
      </section>
    </div>

    <section className="section mt-4">
      <div className="section-header"><div><h3>Security activity</h3><p>Recent authentication, access, settings, and destructive events.</p></div></div>
      <div className="table-scroll"><table className="data-table audit-table"><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Outcome</th><th>Source</th></tr></thead><tbody>
        {audit.data?.events.map((event) => <tr key={event.id}><td className="table-secondary">{new Date(event.occurred_at).toLocaleString()}</td><td><div className="table-primary">{event.actor_type}</div><div className="table-secondary font-mono">{event.actor_id || "anonymous"}</div></td><td className="font-mono text-xs">{event.action}</td><td><Badge variant={event.outcome === "success" ? "success" : "destructive"}>{event.outcome}</Badge></td><td className="table-secondary font-mono">{event.source_ip || "unknown"}</td></tr>)}
        {audit.isPending && <tr><td colSpan={5} className="empty-cell">Loading security activity…</td></tr>}
        {audit.isError && <tr><td colSpan={5} className="empty-cell text-destructive">Could not load security activity.</td></tr>}
        {!audit.isPending && !audit.isError && !audit.data?.events.length && <tr><td colSpan={5} className="empty-cell">No security events recorded.</td></tr>}
      </tbody></table></div>
    </section>

    <Dialog open={Boolean(createdSecret)}><DialogContent className="machine-key-dialog" onEscapeKeyDown={(event) => event.preventDefault()} onPointerDownOutside={(event) => event.preventDefault()}>
      <DialogTitle>Copy this machine key now</DialogTitle>
      <DialogDescription className="text-sm text-muted-foreground">The secret is stored only as a digest and cannot be shown again. Save it before closing this dialog.</DialogDescription>
      <div className="secret-once"><code tabIndex={0} aria-label="New machine API key">{createdSecret}</code><Button type="button" size="sm" variant="outline" aria-label={keyCopied ? "Key copied" : "Copy key"} onClick={copyCreatedKey}>{keyCopied ? <IconCheck size={16} /> : <IconCopy size={16} />}{keyCopied ? "Copied" : "Copy key"}</Button></div>
      <div className="machine-key-actions"><span aria-live="polite">{keyCopied ? "Copied to clipboard" : "This is the only time the full key is shown."}</span><Button type="button" onClick={() => { setCreatedSecret(""); setKeyCopied(false) }}>I saved the key</Button></div>
    </DialogContent></Dialog>
  </div>
}
