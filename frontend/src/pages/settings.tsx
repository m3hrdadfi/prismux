import {
  IconActivityHeartbeat, IconAlertTriangle, IconArrowsShuffle, IconCheck, IconChevronDown,
  IconChevronUp, IconCoin, IconCopy, IconDatabase, IconEye, IconEyeOff, IconKey,
  IconPlus, IconRefresh, IconSearch, IconServer, IconSettings, IconShieldCheck, IconTrash,
} from "@tabler/icons-react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import { api } from "@/lib/api"
import type { PricingEntry, ProviderConfig, ProviderUpdate, RouteTarget, RuntimeSettings, SettingsUpdate } from "@/lib/types"
import { cn } from "@/lib/utils"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogTitle } from "@/components/ui/dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { PageHeading } from "@/components/page-heading"
import { Skeleton } from "@/components/ui/skeleton"
import { copyText } from "@/lib/clipboard"

export type SectionId = "providers" | "routing" | "pricing" | "storage" | "alerts"
type HeaderRow = { name: string; value: string }
type RouteRow = { alias: string; targets: RouteTarget[] }

const sectionMeta: Record<SectionId, { title: string; description: string }> = {
  providers: { title: "Providers", description: "Manage simultaneous upstream connections, credentials, models, and capacity." },
  routing: { title: "Model routing", description: "Map client aliases to primary targets and ordered provider fallbacks." },
  pricing: { title: "Pricing", description: "Maintain input and output token costs for every provider and model." },
  storage: { title: "Storage", description: "Control payload, statistics, and queue-sample retention." },
  alerts: { title: "Alerts", description: "Set thresholds for queue pressure, failures, and RPM utilization." },
}

function providerDraft(provider: ProviderConfig): ProviderUpdate {
  const { api_key_configured: _key, health: _health, health_error: _error, last_checked_at: _checked, models: _models, models_updated_at: _updated, ...config } = provider
  return { ...config, api_key: "", clear_api_key: false }
}

function newProvider(presets: Record<string, { name: string; adapter: string; base_url: string; models_url?: string }>, index: number): ProviderUpdate {
  const preset = presets.openai || Object.values(presets)[0]
  return {
    id: `provider-${index}`, name: preset?.name || "New provider", preset: presets.openai ? "openai" : "custom",
    adapter: (preset?.adapter || "openai-compatible") as ProviderUpdate["adapter"], base_url: preset?.base_url || "",
    models_url: preset?.models_url || "", enabled: true, is_default: false, default_model: "", rate_limit_rpm: 40,
    request_burst: 1, tokens_per_minute: null, token_burst: null, max_concurrency: null,
    timeout_seconds: 120, anthropic_version: "2023-06-01", headers: {}, api_key: "", clear_api_key: false,
  }
}

export function SettingsPage({ section }: { section: SectionId }) {
  const providers = useQuery({ queryKey: ["providers"], queryFn: api.providers })
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings, enabled: section === "storage" || section === "alerts" })
  const routes = useQuery({ queryKey: ["routes"], queryFn: api.routes, enabled: section === "routing" })
  const pricing = useQuery({ queryKey: ["pricing"], queryFn: api.pricing, enabled: section === "pricing" })
  const needsSettings = section === "storage" || section === "alerts"
  const loading = providers.isLoading || (needsSettings && settings.isLoading) || (section === "routing" && routes.isLoading) || (section === "pricing" && pricing.isLoading)

  if (loading) {
    return <div className="page"><PageHeading title={sectionMeta[section].title} description="Loading configuration." /><Skeleton className="mt-5 h-[620px]" /></div>
  }
  const error = providers.error || (needsSettings ? settings.error : null) || (section === "routing" ? routes.error : null) || (section === "pricing" ? pricing.error : null)
  const incomplete = !providers.data || (needsSettings && !settings.data) || (section === "routing" && !routes.data) || (section === "pricing" && !pricing.data)
  if (error || incomplete) {
    return <div className="page"><PageHeading title={sectionMeta[section].title} description={sectionMeta[section].description} /><div className="error-box">Could not load settings: {error?.message || "Incomplete response"}</div></div>
  }

  return <div className="page">
    <PageHeading title={sectionMeta[section].title} description={sectionMeta[section].description} actions={<Badge variant="success"><IconCheck size={13} />PostgreSQL persistence active</Badge>} />
    {!providers.data.encryption.available && <div className="error-box mb-4 flex items-start gap-2"><IconAlertTriangle className="mt-0.5 shrink-0" size={16} /><div><strong>Credential encryption is not configured.</strong><p className="mt-1 text-xs">Set SETTINGS_ENCRYPTION_KEY before adding or replacing provider secrets.</p></div></div>}
    <div className="settings-panels">
      {section === "providers" && <ProvidersPanel response={providers.data} />}
      {section === "routing" && routes.data && <RoutingPanel providers={providers.data.providers} initial={routes.data.routes} />}
      {section === "pricing" && pricing.data && <PricingPanel providers={providers.data.providers} initial={pricing.data.prices} />}
      {section === "storage" && settings.data && <GlobalPanel mode="storage" settings={settings.data.settings} />}
      {section === "alerts" && settings.data && <GlobalPanel mode="alerts" settings={settings.data.settings} />}
    </div>
  </div>
}

function ProvidersPanel({ response }: { response: Awaited<ReturnType<typeof api.providers>> }) {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState(response.providers[0]?.id || "")
  const [creating, setCreating] = useState(false)
  const selected = response.providers.find((provider) => provider.id === selectedId) || response.providers[0]
  const [draft, setDraft] = useState<ProviderUpdate>(() => selected ? providerDraft(selected) : newProvider(response.presets, 1))
  const [showSecret, setShowSecret] = useState(false)
  const [publicHeaders, setPublicHeaders] = useState<HeaderRow[]>([])
  const [secretHeaders, setSecretHeaders] = useState<HeaderRow[]>([])
  const [clientUrlCopied, setClientUrlCopied] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)

  useEffect(() => {
    if (!creating && selected) {
      setDraft(providerDraft(selected))
      setPublicHeaders(Object.entries(selected.headers).map(([name, value]) => ({ name, value })))
      setSecretHeaders([])
    }
  }, [selectedId, selected, creating])

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["providers"] })
  const save = useMutation({
    mutationFn: (value: ProviderUpdate) => creating ? api.createProvider(value) : api.updateProvider(value.id, value),
    onSuccess: (provider) => { setCreating(false); setSelectedId(provider.id); refresh(); queryClient.invalidateQueries({ queryKey: ["settings"] }); queryClient.invalidateQueries({ queryKey: ["stats"] }); toast.success("Provider saved and applied") },
    onError: (error) => toast.error(error.message),
  })
  const remove = useMutation({ mutationFn: api.deleteProvider, onSuccess: () => { setDeleteOpen(false); setSelectedId(response.providers[0]?.id || ""); refresh(); toast.success("Provider deleted") }, onError: (error) => toast.error(error.message) })
  const discover = useMutation({
    mutationFn: ({ id, values }: { id: string; values: ProviderUpdate }) => api.discoverProviderModels(id, {
      base_url: values.base_url, models_url: values.models_url, adapter: values.adapter,
      api_key: values.api_key, headers: values.headers, anthropic_version: values.anthropic_version,
    }),
    onSuccess: (data) => {
      if (!draft.default_model && data.models[0]) update("default_model", data.models[0])
      refresh()
      toast.success(`${data.models.length} models loaded from ${data.models_url}`)
    },
    onError: (error) => toast.error(error.message),
  })
  const health = useMutation({ mutationFn: api.checkProvider, onSuccess: () => { refresh(); toast.success("Provider connection is healthy") }, onError: (error) => toast.error(error.message) })

  function update<K extends keyof ProviderUpdate>(key: K, value: ProviderUpdate[K]) { setDraft((current) => ({ ...current, [key]: value })) }
  function choosePreset(presetId: string) { const preset = response.presets[presetId]; if (preset) setDraft((current) => ({ ...current, preset: presetId, name: creating ? preset.name : current.name, adapter: preset.adapter as ProviderUpdate["adapter"], base_url: preset.base_url, models_url: preset.models_url || `${preset.base_url.replace(/\/$/, "")}/models` })) }
  function submit() {
    const headers = Object.fromEntries(publicHeaders.map((row) => [row.name.trim(), row.value]).filter(([name]) => name))
    const secrets = Object.fromEntries(secretHeaders.map((row) => [row.name.trim(), row.value]).filter(([name, value]) => name && value))
    if (!draft.id.trim() || !draft.name.trim() || !draft.base_url.trim()) return toast.error("Provider ID, name, and base URL are required")
    save.mutate({ ...draft, id: draft.id.trim(), name: draft.name.trim(), headers, ...(secretHeaders.length ? { secret_headers: secrets } : {}) })
  }
  function startNew(source?: ProviderConfig) {
    setCreating(true)
    const value = source ? { ...providerDraft(source), id: `${source.id}-copy`, name: `${source.name} copy`, is_default: false, api_key: "", clear_api_key: false } : newProvider(response.presets, response.providers.length + 1)
    setDraft(value); setPublicHeaders(Object.entries(value.headers).map(([name, headerValue]) => ({ name, value: headerValue }))); setSecretHeaders([])
  }

  const current = selected && !creating ? selected : undefined
  const modelOptions = current?.models || []
  const directClientUrl = current ? `${window.location.origin}/${current.id}/v1` : ""
  const copyClientUrl = async () => {
    try {
      await copyText(directClientUrl)
      setClientUrlCopied(true)
      window.setTimeout(() => setClientUrlCopied(false), 1600)
      toast.success("Direct client URL copied")
    } catch {
      setClientUrlCopied(false)
      toast.error("Copy failed. Select the URL and copy it manually.")
    }
  }
  return <section className="section overflow-hidden">
    <div className="section-header"><div><h3>Providers</h3><p>Independent credentials, models, capacity, concurrency, and health.</p></div><Button type="button" size="sm" onClick={() => startNew()}><IconPlus size={15} />Add provider</Button></div>
    <div className="provider-workspace">
      <aside className="provider-list" aria-label="Configured providers">{response.providers.map((provider) => <button key={provider.id} type="button" className="provider-list-item" data-state={!creating && selectedId === provider.id ? "active" : "inactive"} onClick={() => { setCreating(false); setSelectedId(provider.id) }}><span className={cn("health-mark", `health-${provider.health}`)} /><span className="min-w-0 flex-1"><strong>{provider.name}</strong><small>{provider.preset}{provider.is_default ? " / default" : ""}</small></span>{!provider.enabled && <Badge variant="secondary">Off</Badge>}</button>)}</aside>
      <div className="provider-editor">
        <div className="provider-editor-head"><div><div className="flex items-center gap-2"><h4>{creating ? "New provider" : draft.name}</h4>{current && <Badge variant={current.health === "healthy" ? "success" : "secondary"}>{current.health}</Badge>}</div><p className="font-mono text-xs text-muted-foreground">{draft.id || "provider-id"}</p></div><div className="provider-editor-actions">{current && <Button type="button" variant="outline" size="sm" onClick={() => startNew(current)}><IconCopy size={14} />Duplicate</Button>}<Button type="button" size="sm" disabled={save.isPending} onClick={submit}><IconSettings size={14} />{save.isPending ? "Saving" : "Save"}</Button></div></div>
        <EditorGroup title="Connection" description="Identity, adapter, endpoint, and encrypted credentials.">
          <div className="field-grid">
            <Field label="Provider preset"><select className="select" value={draft.preset} onChange={(event) => choosePreset(event.target.value)}>{Object.entries(response.presets).map(([id, preset]) => <option key={id} value={id}>{preset.name}</option>)}</select></Field>
            <Field label="Adapter"><select className="select" value={draft.adapter} onChange={(event) => update("adapter", event.target.value as ProviderUpdate["adapter"])}><option value="openai-compatible">OpenAI-compatible</option><option value="anthropic">Anthropic native</option></select></Field>
            <Field label="Provider ID" help="Used in provider_id::model_id selectors."><Input disabled={!creating} value={draft.id} onChange={(event) => update("id", event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))} /></Field>
            <Field label="Display name"><Input value={draft.name} onChange={(event) => update("name", event.target.value)} /></Field>
            <Field label="Base URL" className="col-span-full"><Input value={draft.base_url} onChange={(event) => update("base_url", event.target.value)} /></Field>
            <Field label="Models URL" help="Leave blank to derive it from the base URL." className="col-span-full"><Input value={draft.models_url} onChange={(event) => update("models_url", event.target.value)} /></Field>
            {current && <Field label="Direct client URL" help="Use this OpenAI-compatible base URL to bypass the default provider and routes." className="col-span-full"><div className="client-url-row"><code tabIndex={0}>{directClientUrl}</code><Button type="button" variant="outline" size="sm" aria-label={clientUrlCopied ? "Direct client URL copied" : "Copy direct client URL"} onClick={copyClientUrl}>{clientUrlCopied ? <IconCheck size={15} /> : <IconCopy size={15} />}{clientUrlCopied ? "Copied" : "Copy"}</Button></div></Field>}
            {current?.network_policy && <div className={cn("col-span-full network-policy", current.network_policy.status === "denied" && "denied")}><div><strong>{current.network_policy.status === "allowed" ? "Outbound destination approved" : "Outbound destination denied"}</strong><p>{current.network_policy.error || current.network_policy.destinations.map((item) => `${item.hostname}:${item.port} · ${item.classification}`).join(" · ")}</p></div>{current.network_policy.destinations.length > 0 && <code>{current.network_policy.destinations.flatMap((item) => item.addresses).join(", ")}</code>}</div>}
            <Field label="API key" help={current?.api_key_configured ? "A key is encrypted in PostgreSQL. Leave blank to keep it, or type a replacement." : "Optional for local providers such as Ollama."} className="col-span-full"><div className="secret-row"><Input type={showSecret ? "text" : "password"} value={draft.api_key} disabled={draft.clear_api_key} placeholder={current?.api_key_configured ? "•••••••• · Leave blank to keep, type to replace" : "Enter API key"} onChange={(event) => update("api_key", event.target.value)} /><Button type="button" variant="outline" disabled={!draft.api_key} onClick={() => setShowSecret((value) => !value)}>{showSecret ? <IconEyeOff size={15} /> : <IconEye size={15} />}{showSecret ? "Hide" : "Show"}</Button></div>{current?.api_key_configured && <label className="checkbox-row"><input type="checkbox" checked={draft.clear_api_key} onChange={(event) => update("clear_api_key", event.target.checked)} /><span>Remove the encrypted API key when saved</span></label>}</Field>
            <label className="checkbox-row"><input type="checkbox" checked={draft.enabled} onChange={(event) => update("enabled", event.target.checked)} /><span>Provider enabled</span></label>
            <label className="checkbox-row"><input type="checkbox" checked={draft.is_default} disabled={current?.is_default} onChange={(event) => update("is_default", event.target.checked)} /><span>Use as default provider</span></label>
          </div>
        </EditorGroup>
        <EditorGroup title="Models" description="Cached models populate defaults, routes, pricing, and tests."><Field label="Default model" help={current?.models_updated_at ? `Catalog refreshed ${current.models_updated_at} UTC` : "Refresh models to populate the selector."}><div className="model-picker"><select className="select" aria-label="Discovered model" value={modelOptions.includes(draft.default_model) ? draft.default_model : ""} disabled={!modelOptions.length} onChange={(event) => update("default_model", event.target.value)}><option value="">{modelOptions.length ? "Select a discovered model" : "No discovered models"}</option>{modelOptions.map((model) => <option key={model} value={model}>{model}</option>)}</select><Input aria-label="Custom model ID" value={draft.default_model} placeholder="Or enter a model ID" onChange={(event) => update("default_model", event.target.value)} /><Button type="button" variant="outline" disabled={!current || discover.isPending || !draft.base_url.trim()} onClick={() => current && discover.mutate({ id: current.id, values: draft })}><IconRefresh className={discover.isPending ? "animate-spin" : ""} size={15} />{discover.isPending ? "Loading models" : "Refresh models"}</Button></div></Field>{current && <div className="mt-3 flex items-center justify-between rounded-[8px] bg-muted p-3 text-xs"><span>{current.models.length} cached models</span><Button type="button" variant="ghost" size="sm" disabled={health.isPending} onClick={() => health.mutate(current.id)}><IconActivityHeartbeat size={15} />Test connection</Button></div>}</EditorGroup>
        <EditorGroup title="Capacity" description="Leave token and concurrency limits blank to disable them."><div className="field-grid three"><NumberField label="Requests per minute" value={draft.rate_limit_rpm} onChange={(value) => update("rate_limit_rpm", value)} /><NumberField label="Request burst" value={draft.request_burst} onChange={(value) => update("request_burst", value || 1)} /><NumberField label="Tokens per minute" value={draft.tokens_per_minute} optional onChange={(value) => update("tokens_per_minute", value)} /><NumberField label="Token burst" value={draft.token_burst} optional onChange={(value) => update("token_burst", value)} /><NumberField label="Max concurrency" value={draft.max_concurrency} optional integer onChange={(value) => update("max_concurrency", value)} /><NumberField label="Timeout seconds" value={draft.timeout_seconds} onChange={(value) => update("timeout_seconds", value || 120)} /></div></EditorGroup>
        <EditorGroup title="Advanced headers" description="Secret values are encrypted and never returned by the API."><HeaderEditor rows={publicHeaders} onChange={setPublicHeaders} secret={false} /><div className="mt-5 border-t border-border pt-4"><div className="mb-2 flex items-center gap-2 text-sm font-semibold"><IconKey size={15} />Secret headers</div><HeaderEditor rows={secretHeaders} onChange={setSecretHeaders} secret /></div>{draft.adapter === "anthropic" && <Field label="Anthropic API version" className="mt-4"><Input value={draft.anthropic_version} onChange={(event) => update("anthropic_version", event.target.value)} /></Field>}</EditorGroup>
        {current && <div className="danger-zone"><div><strong>Delete provider</strong><p>Routes must be reassigned first. The default provider cannot be deleted.</p></div><Button type="button" variant="destructive" disabled={current.is_default} onClick={() => setDeleteOpen(true)}><IconTrash size={15} />Delete</Button></div>}
      </div>
    </div>
    <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}><AlertDialogContent><AlertDialogTitle>Delete {current?.name}?</AlertDialogTitle><AlertDialogDescription>This removes its configuration, credentials, cached models, and pricing. Request history remains.</AlertDialogDescription><div className="flex justify-end gap-2"><AlertDialogCancel asChild><Button variant="outline">Cancel</Button></AlertDialogCancel><AlertDialogAction asChild><Button variant="destructive" disabled={remove.isPending} onClick={() => current && remove.mutate(current.id)}>Delete provider</Button></AlertDialogAction></div></AlertDialogContent></AlertDialog>
  </section>
}

function RoutingPanel({ providers, initial }: { providers: ProviderConfig[]; initial: Record<string, RouteTarget[]> }) {
  const queryClient = useQueryClient()
  const [rows, setRows] = useState<RouteRow[]>(() => Object.entries(initial).map(([alias, targets]) => ({ alias, targets })))
  const save = useMutation({ mutationFn: api.updateRoutes, onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["routes"] }); toast.success("Model routes saved and applied") }, onError: (error) => toast.error(error.message) })
  const modelsFor = (providerId: string) => providers.find((provider) => provider.id === providerId)?.models || []
  const changeTarget = (rowIndex: number, targetIndex: number, value: Partial<RouteTarget>) => setRows((current) => current.map((row, ri) => ri === rowIndex ? { ...row, targets: row.targets.map((target, ti) => ti === targetIndex ? { ...target, ...value } : target) } : row))
  const moveTarget = (rowIndex: number, targetIndex: number, direction: -1 | 1) => setRows((current) => current.map((row, ri) => { if (ri !== rowIndex) return row; const targets = [...row.targets]; const next = targetIndex + direction; if (next < 0 || next >= targets.length) return row; [targets[targetIndex], targets[next]] = [targets[next], targets[targetIndex]]; return { ...row, targets } }))
  function submit() { const normalized = Object.fromEntries(rows.map((row) => [row.alias.trim(), row.targets.map((target) => ({ provider_id: target.provider_id, model: target.model.trim() }))])); if (rows.some((row) => !row.alias.trim() || row.alias.includes("::") || !row.targets.length || row.targets.some((target) => !target.provider_id || !target.model.trim()))) return toast.error("Every route needs a valid alias and at least one complete target"); if (new Set(rows.map((row) => row.alias.trim())).size !== rows.length) return toast.error("Route aliases must be unique"); save.mutate(normalized) }
  return <SettingsSection title="Model routing" description="Map aliases to a primary provider and ordered fallback targets." icon={<IconArrowsShuffle size={18} />} action={<Button size="sm" onClick={submit} disabled={save.isPending || !rows.length}><IconSettings size={14} />Save routes</Button>}>
    {!rows.length && <EmptyState title="No model routes" description="Raw model IDs currently use the default provider." action="Add route" onAction={() => setRows([{ alias: "", targets: [{ provider_id: providers[0]?.id || "", model: "" }] }])} />}
    <div className="route-editor-list">{rows.map((row, rowIndex) => <article className="route-editor" key={rowIndex}><div className="route-alias-row"><Field label="Client alias"><Input value={row.alias} placeholder="quality" onChange={(event) => setRows((current) => current.map((item, index) => index === rowIndex ? { ...item, alias: event.target.value } : item))} /></Field><Button type="button" variant="ghost" size="icon" aria-label={`Delete route ${row.alias || rowIndex + 1}`} onClick={() => setRows((current) => current.filter((_, index) => index !== rowIndex))}><IconTrash size={15} /></Button></div><div className="target-list">{row.targets.map((target, targetIndex) => <div className="target-row" key={targetIndex}><span className="target-priority">{targetIndex === 0 ? "Primary" : `Fallback ${targetIndex}`}</span><Field label="Provider"><select className="select" value={target.provider_id} onChange={(event) => changeTarget(rowIndex, targetIndex, { provider_id: event.target.value, model: "" })}>{providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}{provider.enabled ? "" : " (disabled)"}</option>)}</select></Field><Field label="Model ID"><ModelCombobox id={`route-model-${rowIndex}-${targetIndex}`} ariaLabel={`Model for ${row.alias || `route ${rowIndex + 1}`} target ${targetIndex + 1}`} options={modelsFor(target.provider_id).map((model) => ({ value: model, label: model }))} value={target.model} onChange={(value) => changeTarget(rowIndex, targetIndex, { model: value })} searchPlaceholder="Search models" emptyLabel="model" /></Field><div className="target-actions"><Button type="button" variant="ghost" size="icon" aria-label="Move target up" disabled={targetIndex === 0} onClick={() => moveTarget(rowIndex, targetIndex, -1)}><IconChevronUp size={14} /></Button><Button type="button" variant="ghost" size="icon" aria-label="Move target down" disabled={targetIndex === row.targets.length - 1} onClick={() => moveTarget(rowIndex, targetIndex, 1)}><IconChevronDown size={14} /></Button><Button type="button" variant="ghost" size="icon" aria-label="Delete target" disabled={row.targets.length === 1} onClick={() => setRows((current) => current.map((item, index) => index === rowIndex ? { ...item, targets: item.targets.filter((_, index) => index !== targetIndex) } : item))}><IconTrash size={14} /></Button></div></div>)}</div><Button type="button" variant="outline" size="sm" onClick={() => setRows((current) => current.map((item, index) => index === rowIndex ? { ...item, targets: [...item.targets, { provider_id: providers[0]?.id || "", model: "" }] } : item))}><IconPlus size={14} />Add fallback</Button></article>)}</div>
    {!!rows.length && <Button type="button" variant="outline" className="mt-4" onClick={() => setRows((current) => [...current, { alias: "", targets: [{ provider_id: providers[0]?.id || "", model: "" }] }])}><IconPlus size={15} />Add route</Button>}
  </SettingsSection>
}

function PricingPanel({ providers, initial }: { providers: ProviderConfig[]; initial: PricingEntry[] }) {
  const queryClient = useQueryClient()
  const [rows, setRows] = useState(initial.map((item) => ({ ...item, input_per_1m: String(item.input_per_1m), output_per_1m: String(item.output_per_1m) })))
  const save = useMutation({ mutationFn: api.updatePricing, onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["pricing"] }); toast.success("Provider pricing saved") }, onError: (error) => toast.error(error.message) })
  const updateRow = (index: number, value: Partial<(typeof rows)[number]>) => setRows((current) => current.map((item, ri) => ri === index ? { ...item, ...value } : item))
  function submit() { const values = rows.map((row) => ({ provider_id: row.provider_id, model_id: row.model_id.trim(), input_per_1m: Number(row.input_per_1m), output_per_1m: Number(row.output_per_1m) })); if (values.some((row) => !row.provider_id || !row.model_id || !Number.isFinite(row.input_per_1m) || row.input_per_1m < 0 || !Number.isFinite(row.output_per_1m) || row.output_per_1m < 0)) return toast.error("Every pricing row needs a provider, model, and non-negative rates"); if (new Set(values.map((row) => `${row.provider_id}::${row.model_id}`)).size !== values.length) return toast.error("Provider and model pricing pairs must be unique"); save.mutate(values) }
  return <SettingsSection title="Provider pricing" description="Enter the provider's published input and output prices per 1 million tokens." icon={<IconCoin size={18} />} action={<Button size="sm" onClick={submit} disabled={save.isPending || !rows.length}><IconSettings size={14} />Save pricing</Button>}>
    {!!rows.length && <div className="config-row pricing-multi config-head"><span>Provider</span><span>Model ID</span><span>Input / 1M</span><span>Output / 1M</span><span>Action</span></div>}
    {!rows.length && <EmptyState title="No provider pricing" description="Request costs remain unavailable until rates are added." action="Add pricing" onAction={() => setRows([{ provider_id: providers[0]?.id || "", model_id: "default", input_per_1m: "0", output_per_1m: "0" }])} />}
    {rows.map((row, index) => <div className="config-row pricing-multi" key={index}>
      <div className="config-field"><span>Provider</span><select className="select" aria-label={`Pricing provider ${index + 1}`} value={row.provider_id} onChange={(event) => updateRow(index, { provider_id: event.target.value, model_id: "default" })}>{providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</select></div>
      <div className="config-field"><span>Model ID</span><PricingModelPicker provider={providers.find((provider) => provider.id === row.provider_id)} value={row.model_id} index={index} onChange={(model_id) => updateRow(index, { model_id })} /></div>
      <div className="config-field"><span>Input / 1M</span><Input aria-label={`Input cost per 1M for row ${index + 1}`} type="number" min="0" step="0.000001" value={row.input_per_1m} onChange={(event) => updateRow(index, { input_per_1m: event.target.value })} /></div>
      <div className="config-field"><span>Output / 1M</span><Input aria-label={`Output cost per 1M for row ${index + 1}`} type="number" min="0" step="0.000001" value={row.output_per_1m} onChange={(event) => updateRow(index, { output_per_1m: event.target.value })} /></div>
      <Button aria-label={`Delete pricing row ${index + 1}`} type="button" variant="ghost" size="icon" onClick={() => setRows((current) => current.filter((_, ri) => ri !== index))}><IconTrash size={15} /></Button>
    </div>)}
    {!!rows.length && <Button type="button" variant="outline" className="mt-3" onClick={() => setRows((current) => [...current, { provider_id: providers[0]?.id || "", model_id: "", input_per_1m: "0", output_per_1m: "0" }])}><IconPlus size={15} />Add pricing</Button>}
  </SettingsSection>
}

function ModelCombobox({ id, ariaLabel, customAriaLabel, options, value, onChange, searchPlaceholder = "Search models", customLabel = "Custom model ID…", emptyLabel = "model" }: { id: string; ariaLabel: string; customAriaLabel?: string; options: { value: string; label: string }[]; value: string; onChange: (value: string) => void; searchPlaceholder?: string; customLabel?: string; emptyLabel?: string }) {
  const isListed = options.some((option) => option.value === value)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const rootRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const filtered = options.filter((option) => option.label.toLowerCase().includes(query.trim().toLowerCase()))
  const selected = options.find((option) => option.value === value)
  const selectedLabel = selected ? selected.label : value || `Custom ${emptyLabel} ID`

  useEffect(() => {
    if (!open) return
    const close = (event: PointerEvent) => { if (!rootRef.current?.contains(event.target as Node)) setOpen(false) }
    document.addEventListener("pointerdown", close)
    return () => document.removeEventListener("pointerdown", close)
  }, [open])

  useEffect(() => {
    if (open) requestAnimationFrame(() => searchRef.current?.focus())
  }, [open])

  function choose(next: string) {
    onChange(next === "__custom__" ? "" : next)
    setOpen(false)
    setQuery("")
  }

  return <div className="pricing-model-picker">
    <div className="model-combobox" ref={rootRef}>
      <button type="button" className="model-combobox-trigger" role="combobox" aria-label={ariaLabel} aria-expanded={open} aria-controls={`${id}-options`} onClick={() => setOpen((current) => !current)}>
        <span title={selectedLabel}>{selectedLabel}</span><IconChevronDown size={16} aria-hidden="true" />
      </button>
      {open && <div className="model-combobox-popover">
        <label className="model-combobox-search"><IconSearch size={15} aria-hidden="true" /><input ref={searchRef} type="search" value={query} aria-label={`Search ${ariaLabel}`} placeholder={searchPlaceholder} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") setOpen(false) }} /></label>
        <div className="model-combobox-list" role="listbox" id={`${id}-options`} aria-label={ariaLabel}>
          {filtered.map((option) => <button type="button" role="option" aria-selected={isListed && value === option.value} className="model-combobox-option" key={option.value} onClick={() => choose(option.value)}><span>{option.label}</span>{isListed && value === option.value && <IconCheck size={15} aria-hidden="true" />}</button>)}
          {!filtered.length && <p className="model-combobox-empty">No cached models match “{query}”.</p>}
        </div>
        <button type="button" className="model-combobox-custom" onClick={() => choose("__custom__")}><IconPlus size={15} aria-hidden="true" /><span>{customLabel}</span></button>
      </div>}
    </div>
    {!isListed && <Input aria-label={customAriaLabel || `Custom ${ariaLabel}`} value={value} autoFocus placeholder={`Enter ${emptyLabel} ID`} onChange={(event) => onChange(event.target.value)} />}
  </div>
}

function PricingModelPicker({ provider, value, index, onChange }: { provider?: ProviderConfig; value: string; index: number; onChange: (value: string) => void }) {
  const models = [...new Set([provider?.default_model, ...(provider?.models || [])].filter(Boolean) as string[])]
  const options = [{ value: "default", label: "Default fallback" }, ...models.map((model) => ({ value: model, label: model }))]
  return <ModelCombobox id={`pricing-model-options-${index}`} ariaLabel={`Pricing model ${index + 1}`} customAriaLabel={`Custom pricing model ${index + 1}`} options={options} value={value} onChange={onChange} searchPlaceholder={`Search ${provider?.name || "provider"} models`} emptyLabel="model" />
}

function GlobalPanel({ mode, settings }: { mode: "storage" | "alerts"; settings: RuntimeSettings }) {
  const queryClient = useQueryClient(); const [draft, setDraft] = useState(settings)
  const save = useMutation({ mutationFn: (values: SettingsUpdate) => api.updateSettings(values), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["settings"] }); toast.success("Global settings saved") }, onError: (error) => toast.error(error.message) })
  const updateNumber = (key: keyof RuntimeSettings, value: number) => setDraft((current) => ({ ...current, [key]: value }))
  const submit = () => save.mutate({ ...draft, api_key: "", clear_api_key: false })
  if (mode === "storage") return <SettingsSection title="Storage" description="Control payload, statistics, and capacity-sample retention globally." icon={<IconDatabase size={18} />} action={<Button size="sm" onClick={submit}><IconSettings size={14} />Save storage</Button>}><div className="field-grid three"><NumberField label="Queue history hours" value={draft.retention_hours} onChange={(value) => updateNumber("retention_hours", value || 24)} /><NumberField label="Payload days" value={draft.payload_retention_days} onChange={(value) => updateNumber("payload_retention_days", value || 7)} /><NumberField label="Statistics days" value={draft.stats_retention_days} onChange={(value) => updateNumber("stats_retention_days", value || 365)} /></div></SettingsSection>
  return <SettingsSection title="Alerts" description="Configure global operational warning thresholds." icon={<IconShieldCheck size={18} />} action={<Button size="sm" onClick={submit}><IconSettings size={14} />Save alerts</Button>}><div className="field-grid three"><NumberField label="Sustained queue seconds" value={draft.alert_queue_seconds} onChange={(value) => updateNumber("alert_queue_seconds", value || 30)} /><NumberField label="Error rate percentage" value={draft.alert_error_rate_pct} onChange={(value) => updateNumber("alert_error_rate_pct", value ?? 10)} /><NumberField label="RPM utilization percentage" value={draft.alert_rpm_pct} onChange={(value) => updateNumber("alert_rpm_pct", value ?? 80)} /></div></SettingsSection>
}

function HeaderEditor({ rows, onChange, secret }: { rows: HeaderRow[]; onChange: (rows: HeaderRow[]) => void; secret: boolean }) { return <div><div className="config-row headers config-head"><span>Header name</span><span>{secret ? "Secret value" : "Value"}</span><span>Action</span></div>{rows.map((row, index) => <div className="config-row headers" key={index}><Input value={row.name} onChange={(event) => onChange(rows.map((item, ri) => ri === index ? { ...item, name: event.target.value } : item))} /><Input type={secret ? "password" : "text"} value={row.value} placeholder={secret ? "Encrypted when saved" : "Header value"} onChange={(event) => onChange(rows.map((item, ri) => ri === index ? { ...item, value: event.target.value } : item))} /><Button type="button" variant="ghost" size="icon" onClick={() => onChange(rows.filter((_, ri) => ri !== index))}><IconTrash size={14} /></Button></div>)}<Button type="button" size="sm" variant="outline" className="mt-2" onClick={() => onChange([...rows, { name: "", value: "" }])}><IconPlus size={14} />Add {secret ? "secret " : ""}header</Button></div> }
function SettingsSection({ title, description, icon, action, children }: { title: string; description: string; icon: React.ReactNode; action?: React.ReactNode; children: React.ReactNode }) { return <section className="section"><div className="section-header"><div><h3>{title}</h3><p>{description}</p></div><div className="flex items-center gap-3"><span className="text-muted-foreground">{icon}</span>{action}</div></div><div className="section-body">{children}</div></section> }
function EditorGroup({ title, description, children }: { title: string; description: string; children: React.ReactNode }) { return <section className="editor-group"><div className="editor-group-title"><h5>{title}</h5><p>{description}</p></div><div>{children}</div></section> }
function Field({ label, help, className, children }: { label: string; help?: string; className?: string; children: React.ReactNode }) { return <div className={cn("field", className)}><label>{label}</label>{children}{help && <p className="field-help">{help}</p>}</div> }
function NumberField({ label, value, onChange, optional, integer }: { label: string; value: number | null; onChange: (value: number | null) => void; optional?: boolean; integer?: boolean }) { return <Field label={label} help={optional ? "Leave blank to disable." : undefined}><Input type="number" min="0.01" step={integer ? "1" : "0.01"} value={value ?? ""} onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))} /></Field> }
function EmptyState({ title, description, action, onAction }: { title: string; description: string; action: string; onAction: () => void }) { return <div className="empty-state"><IconServer size={24} /><strong>{title}</strong><p>{description}</p><Button type="button" size="sm" variant="outline" onClick={onAction}><IconPlus size={14} />{action}</Button></div> }
