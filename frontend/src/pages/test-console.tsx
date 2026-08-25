import { zodResolver } from "@hookform/resolvers/zod"
import { IconAlertTriangle, IconBrain, IconCheck, IconChevronDown, IconPlayerPlay, IconPlayerStop, IconTerminal2, IconX } from "@tabler/icons-react"
import { useQuery } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { api, type TestStreamEvent } from "@/lib/api"
import type { ModelCapabilities, TestResult, Usage } from "@/lib/types"
import { formatCost, formatDuration } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input, Textarea } from "@/components/ui/input"
import { PageHeading } from "@/components/page-heading"
import { MarkdownResponse } from "@/components/markdown-response"

const schema = z.object({
  model: z.string().trim().min(1, "Choose or enter a model"),
  content: z.string().trim().min(1, "Enter a message"),
})
type FormValues = z.infer<typeof schema>
type ReasoningMode = "auto" | "on" | "off"

const FALLBACK_CAPABILITIES: ModelCapabilities = {
  streaming: true,
  reasoning_modes: ["auto"],
  default_max_tokens: 512,
  reasoning_control: null,
}

function streamChunk(event: TestStreamEvent) {
  if (event.type !== "chunk") return null
  return event.data as {
    choices?: Array<{ delta?: { content?: string; reasoning_content?: string; reasoning?: string }; finish_reason?: string | null }>
    usage?: Usage
    error?: { message?: string }
  }
}

export function TestConsolePage() {
  const settings = useQuery({ queryKey: ["settings", "test"], queryFn: api.settings })
  const providers = useQuery({ queryKey: ["providers", "test"], queryFn: api.providers })
  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { model: "", content: "" } })
  const [providerId, setProviderId] = useState("")
  const [streamEnabled, setStreamEnabled] = useState(true)
  const [reasoningMode, setReasoningMode] = useState<ReasoningMode>("auto")
  const [outputBudget, setOutputBudget] = useState("512")
  const [result, setResult] = useState<TestResult | null>(null)
  const [requestError, setRequestError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const controllerRef = useRef<AbortController | null>(null)

  const enabledProviders = providers.data?.providers.filter((provider) => provider.enabled) || []
  const selectedProvider = enabledProviders.find((provider) => provider.id === providerId)
  const providerModels = [...new Set([selectedProvider?.default_model, ...(selectedProvider?.models || [])].filter(Boolean) as string[])]
  const qualifiedModels = providerModels.map((model) => ({ id: model, value: `${selectedProvider?.id}::${model}` }))
  const currentModel = form.watch("model")
  const selectedModelValue = qualifiedModels.some((model) => model.value === currentModel) ? currentModel : "__custom__"
  const upstreamModel = currentModel.includes("::") ? currentModel.split("::", 2)[1] : currentModel
  const capabilities = selectedProvider?.model_capabilities?.[upstreamModel] || FALLBACK_CAPABILITIES
  const supportsNativeReasoningOff = capabilities.reasoning_modes.includes("off")

  useEffect(() => {
    if (!providers.data || providerId) return
    const preferred = enabledProviders.find((provider) => provider.is_default) || enabledProviders[0]
    if (!preferred) return
    setProviderId(preferred.id)
    const preferredModel = preferred.default_model || (preferred.models.includes(settings.data?.settings.default_model || "") ? settings.data?.settings.default_model : preferred.models[0])
    if (preferredModel) form.setValue("model", `${preferred.id}::${preferredModel}`)
  }, [providers.data, settings.data, providerId, enabledProviders, form])

  useEffect(() => {
    setOutputBudget(String(capabilities.default_max_tokens))
    setReasoningMode("auto")
  }, [providerId, upstreamModel, capabilities.default_max_tokens])

  useEffect(() => () => controllerRef.current?.abort(), [])

  function chooseProvider(nextProviderId: string) {
    setProviderId(nextProviderId)
    const provider = enabledProviders.find((item) => item.id === nextProviderId)
    const model = provider?.default_model || provider?.models[0] || ""
    form.setValue("model", model ? `${nextProviderId}::${model}` : "", { shouldValidate: form.formState.isSubmitted })
  }

  function handleStreamEvent(event: TestStreamEvent, seed: TestResult) {
    if (event.type === "meta") {
      setResult((current) => ({ ...seed, ...current, ...event.data }))
      return
    }
    if (event.type === "result") {
      setResult(reasoningMode === "off" ? { ...event.data, reasoning_text: null } : event.data)
      return
    }
    const chunk = streamChunk(event)
    if (!chunk) return
    if (chunk.error?.message) setRequestError(chunk.error.message)
    const choice = chunk.choices?.[0]
    const delta = choice?.delta
    setResult((current) => ({
      ...seed,
      ...current,
      text: `${current?.text || ""}${delta?.content || ""}` || null,
      reasoning_text: reasoningMode === "off" ? null : `${current?.reasoning_text || ""}${delta?.reasoning_content || delta?.reasoning || ""}` || null,
      finish_reason: choice?.finish_reason || current?.finish_reason || null,
      usage: chunk.usage || current?.usage,
    }))
  }

  async function submit(values: FormValues) {
    const parsedBudget = Number.parseInt(outputBudget, 10)
    const body = {
      ...values,
      max_tokens: Number.isFinite(parsedBudget) ? Math.min(65_536, Math.max(1, parsedBudget)) : capabilities.default_max_tokens,
      reasoning_mode: reasoningMode === "off" && !supportsNativeReasoningOff ? "auto" as const : reasoningMode,
    }
    const seed: TestResult = { ok: true, status_code: null, wait_ms: 0, model: values.model, text: null, reasoning_text: null, attempts: [] }
    setResult(streamEnabled ? seed : null)
    setRequestError(null)
    setRunning(true)
    const controller = new AbortController()
    controllerRef.current = controller
    try {
      if (streamEnabled) await api.testStream(body, (event) => handleStreamEvent(event, seed), controller.signal)
      else {
        const response = await api.test(body)
        setResult(reasoningMode === "off" ? { ...response, reasoning_text: null } : response)
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        setRequestError("Generation stopped. Partial output has been preserved.")
        setResult((current) => current ? { ...current, ok: false, error: "stopped" } : current)
      } else {
        setRequestError(error instanceof Error ? error.message : "The test request failed")
      }
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null
      setRunning(false)
    }
  }

  const hasVisibleOutput = Boolean(result?.text || result?.reasoning_text)
  const showResponse = running || Boolean(result) || Boolean(requestError)
  const reasoningOptions: ReasoningMode[] = capabilities.reasoning_modes.includes("on") ? ["auto", "on", "off"] : ["auto", "off"]

  return <div className="page">
    <PageHeading title="Test console" description="Send one validated request through the active limiter, routing map, provider connection, and response logging path." />
    <div className={`test-layout ${showResponse ? "test-layout--active" : "test-layout--idle"}`}>
      <form className="section test-request" onSubmit={form.handleSubmit(submit)}>
        <div className="section-header"><div><h3>Request</h3><p>Uses the same path as normal proxy traffic.</p></div><IconTerminal2 size={17} className="text-muted-foreground" /></div>
        <div className="section-body stack">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="field">
              <label htmlFor="test-provider">Provider</label>
              <select id="test-provider" className="select" value={providerId} disabled={running || providers.isLoading || enabledProviders.length === 0} onChange={(event) => chooseProvider(event.target.value)}>
                {providers.isLoading && <option value="">Loading providers...</option>}
                {!providers.isLoading && enabledProviders.length === 0 && <option value="">No enabled providers</option>}
                {enabledProviders.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}
              </select>
              <p className="field-help">Only enabled providers are available for direct tests.</p>
            </div>
            <div className="field">
              <label htmlFor="test-model-choice">Model</label>
              <select id="test-model-choice" className="select" value={selectedModelValue} disabled={running || !selectedProvider} onChange={(event) => form.setValue("model", event.target.value === "__custom__" ? "" : event.target.value, { shouldValidate: form.formState.isSubmitted })}>
                {qualifiedModels.map((model) => <option key={model.value} value={model.value}>{model.id}</option>)}
                <option value="__custom__">Custom model ID</option>
              </select>
              <p className="field-help">{providerModels.length ? `${providerModels.length} cached model${providerModels.length === 1 ? "" : "s"} from ${selectedProvider?.name}.` : "No cached models. Refresh this provider in Settings or enter an ID."}</p>
            </div>
          </div>
          {selectedModelValue === "__custom__" && <div className="field"><label htmlFor="test-model">Custom model ID or route alias</label><Input id="test-model" disabled={running} placeholder={selectedProvider ? `${selectedProvider.id}::model-id` : "provider_id::model_id or route alias"} autoComplete="off" {...form.register("model")} />{form.formState.errors.model && <p className="field-help text-destructive">{form.formState.errors.model.message}</p>}</div>}
          {providers.error && <div className="error-box">Could not load configured providers: {providers.error.message}</div>}
          <div className="field"><label htmlFor="test-message">Message</label><Textarea id="test-message" disabled={running} rows={7} placeholder="Ask the model something specific" {...form.register("content")} />{form.formState.errors.content && <p className="field-help text-destructive">{form.formState.errors.content.message}</p>}</div>

          <div className="grid gap-3 rounded-[8px] bg-muted/70 p-3 sm:grid-cols-3">
            <div className="field"><span className="text-xs font-semibold">Delivery</span><label className="flex h-10 items-center gap-2 text-sm"><input type="checkbox" checked={streamEnabled} disabled={running || !capabilities.streaming} onChange={(event) => setStreamEnabled(event.target.checked)} />Stream response</label><p className="field-help">Show tokens as they arrive.</p></div>
            <div className="field"><label htmlFor="test-reasoning">Reasoning</label><select id="test-reasoning" className="select" value={reasoningMode} disabled={running} onChange={(event) => setReasoningMode(event.target.value as ReasoningMode)}>{reasoningOptions.map((mode) => <option key={mode} value={mode}>{mode === "auto" ? "Provider default" : mode === "on" ? "On" : supportsNativeReasoningOff ? "Off" : "Off · hide trace"}</option>)}</select><p className="field-help">{supportsNativeReasoningOff ? "Controls reasoning for this model family." : reasoningMode === "off" ? "Trace hidden; the provider may still reason internally." : "This provider controls internal reasoning."}</p></div>
            <div className="field"><label htmlFor="test-output-budget">Output budget</label><Input id="test-output-budget" type="number" inputMode="numeric" min={1} max={65_536} disabled={running} value={outputBudget} onChange={(event) => setOutputBudget(event.target.value)} /><p className="field-help">Model-aware default; maximum 65,536.</p></div>
          </div>

          {running ? <Button type="button" variant="destructive" onClick={() => controllerRef.current?.abort()}><IconPlayerStop size={16} />Stop generation</Button> : <Button type="submit" disabled={providers.isLoading || enabledProviders.length === 0}><IconPlayerPlay size={16} />Send test request</Button>}
        </div>
      </form>
      {showResponse && <section className="section test-response" aria-live="polite">
        <div className="section-header"><div><h3>Response</h3><p>Live answer, reasoning, route, usage, and cost.</p></div>{running ? <Badge>Streaming</Badge> : result && <Badge variant={result.ok ? "success" : result.error === "stopped" ? "warning" : "destructive"}>{result.ok ? <IconCheck size={13} /> : <IconX size={13} />}{result.error === "stopped" ? "Stopped" : result.status_code || (result.ok ? "Complete" : "Failed")}</Badge>}</div>
        <div className="section-body">
          {running && !hasVisibleOutput && <div className="response-waiting"><div className="size-4 animate-spin rounded-full border-2 border-muted border-t-primary" /><div><p>Waiting for the first token</p><span>The connection is open. You can stop it at any time.</span></div></div>}
          {requestError && <div className={result?.error === "stopped" ? "mb-4 rounded-[8px] border border-warning/40 bg-warning/10 p-3 text-xs text-warning-foreground" : "error-box mb-4"}>{requestError}</div>}
          {result && <div className="stack">
            {result.reasoning_text && !result.text && <div className="reasoning-live"><div className="reasoning-live-head"><span><IconBrain size={15} />{running ? "Reasoning in progress" : "Reasoning received"}</span>{running && <span className="reasoning-pulse">Live</span>}</div><pre>{result.reasoning_text}</pre></div>}
            {result.text && <MarkdownResponse title={running ? "Answer in progress" : "Assistant response"} subtitle={running ? "Streaming Markdown" : "Rendered Markdown"}>{result.text}</MarkdownResponse>}
            {result.reasoning_text && result.text && <details className="reasoning-disclosure"><summary><span><IconBrain size={15} />Reasoning trace</span><IconChevronDown size={15} /></summary><pre>{result.reasoning_text}</pre></details>}
            <div className="response-facts">
              {result.provider_id && <div><span>Provider</span><strong>{result.provider_id}</strong></div>}
              <div><span>Model</span><strong title={result.upstream_model || result.model}>{result.upstream_model || result.model}</strong></div>
              <div><span>Queue wait</span><strong>{formatDuration(result.wait_ms)}</strong></div>
              {result.usage && <><TokenFact label="Input tokens" tokens={result.usage.prompt_tokens} cost={result.cost?.input} priced={result.cost?.priced} /><TokenFact label="Output tokens" tokens={result.usage.completion_tokens} cost={result.cost?.output} priced={result.cost?.priced} /><TokenFact label="Total tokens" tokens={result.usage.total_tokens} cost={result.cost?.total} priced={result.cost?.priced} /></>}
            </div>
            {result.usage_estimated && <p className="m-0 text-[11px] text-muted-foreground">The provider omitted streamed usage; token counts and cost use a character-based estimate.</p>}
            {result.attempts && result.attempts.length > 0 && <details className="request-details"><summary>Provider attempts <span>{result.attempts.length}</span></summary><div className="grid gap-2 pt-3">{result.attempts.map((attempt, index) => <div key={`${attempt.provider_id}-${index}`} className="grid grid-cols-[auto_1fr_auto] items-center gap-3 rounded-[8px] bg-muted p-3 text-xs"><span className="font-mono text-muted-foreground">{index + 1}</span><div><strong>{attempt.provider_id}</strong><span className="ml-2 font-mono text-muted-foreground">{attempt.upstream_model}</span>{attempt.fallback_reason && <p className="mt-1 text-warning">Fallback: {attempt.fallback_reason}</p>}</div><span className="font-mono">{attempt.status_code || attempt.error_type || "failed"}</span></div>)}</div></details>}
            {result.error && result.error !== "stopped" && <div className="error-box">{result.error}</div>}
            {!running && !result.text && result.reasoning_text && <div className="flex items-start gap-2 rounded-[8px] border border-warning/40 bg-warning/10 p-3 text-xs text-warning-foreground"><IconAlertTriangle className="mt-0.5 shrink-0" size={16} /><div><strong className="block text-foreground">The provider did not return a final answer.</strong><span>{result.finish_reason === "length" ? "It used the entire output budget while reasoning. Increase the budget or choose Off when the model supports it." : "It returned reasoning output without final content."}</span></div></div>}
            {!running && !result.text && !result.reasoning_text && result.ok && <div className="flex items-start gap-2 rounded-[8px] border border-warning/40 bg-warning/10 p-3 text-xs text-warning-foreground"><IconAlertTriangle className="mt-0.5 shrink-0" size={16} /><div><strong className="block text-foreground">The provider returned no displayable text.</strong><span>Inspect the raw payload for tool calls or provider-specific output.</span></div></div>}
            {result.response_payload && <details className="raw-payload"><summary>Raw provider payload</summary><pre className="json-block">{JSON.stringify(result.response_payload, null, 2)}</pre></details>}
          </div>}
        </div>
      </section>}
    </div>
  </div>
}

function TokenFact({ label, tokens, cost, priced }: { label: string; tokens: number | null; cost?: number | null; priced?: boolean }) {
  return <div><span>{label}</span><strong>{tokens ?? "—"}</strong><small>{priced ? `Est. ${formatCost(cost)}` : "Pricing not set"}</small></div>
}
