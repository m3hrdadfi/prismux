import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import App from "@/App"
import { ThemeProvider } from "@/components/theme-provider"

const stats = {
  total_requests: 0, queued: 0, queued_last_hour: 0, token_level: 1, bucket_capacity: 1,
  requests_per_minute: 0, rate_limit_rpm: 40, avg_wait_ms: 0, avg_wait_recent_ms: 0,
  avg_wait_recent_n: 0, avg_response_recent_ms: 0, avg_time_to_first_token_ms: 0,
  throttled_last_hour: 0, last_outcome: null, payload_retention_days: 7,
  provider: { id: "ollama", label: "Ollama" }, cost_today: 0, prompt_tokens_today: 0,
  completion_tokens_today: 0, cost_per_hour: [], alerts: [], by_model: {}, recent: [],
  start_time: 0, uptime_seconds: 60,
}
const settings = {
  settings: { base_url: "http://localhost:11434/v1", models_url: "", default_model: "qwen3:8b", rate_limit_rpm: 40, bucket_capacity: 1, retention_hours: 24, payload_retention_days: 7, stats_retention_days: 365, alert_queue_seconds: 30, alert_error_rate_pct: 10, alert_rpm_pct: 80 },
  configuration: { model_routes: { qwen: "qwen3:8b" }, pricing_models: { "qwen3:8b": { input_per_1m: 0, output_per_1m: 0 } } },
  provider: { id: "ollama", label: "Ollama" }, resolved_models_url: "http://localhost:11434/v1/models", api_key_configured: true,
  deployment: { proxy_port: 8100, database: "PostgreSQL" },
}
const providers = {
  providers: [{
    id: "ollama", name: "Local Ollama", preset: "ollama", adapter: "openai-compatible",
    base_url: "http://localhost:11434/v1", models_url: "", enabled: true, is_default: true,
    default_model: "qwen3:8b", rate_limit_rpm: 40, request_burst: 1, tokens_per_minute: null,
    token_burst: null, max_concurrency: null, timeout_seconds: 120, anthropic_version: "2023-06-01",
    headers: {}, api_key_configured: true, health: "healthy", health_error: "", last_checked_at: null,
    models: ["qwen3:8b"], models_updated_at: "2026-08-24 12:00:00",
    model_capabilities: { "qwen3:8b": { streaming: true, stream_usage: true, reasoning_modes: ["auto", "on", "off"], default_max_tokens: 4096, reasoning_control: "enable_thinking" } },
  }, {
    id: "openai", name: "OpenAI", preset: "openai", adapter: "openai-compatible",
    base_url: "https://api.openai.com/v1", models_url: "https://api.openai.com/v1/models", enabled: true, is_default: false,
    default_model: "gpt-5-mini", rate_limit_rpm: 500, request_burst: 10, tokens_per_minute: null,
    token_burst: null, max_concurrency: null, timeout_seconds: 120, anthropic_version: "2023-06-01",
    headers: {}, api_key_configured: true, health: "healthy", health_error: "", last_checked_at: null,
    models: ["gpt-5-mini", "gpt-5"], models_updated_at: "2026-08-24 12:00:00",
    model_capabilities: { "gpt-5-mini": { streaming: true, stream_usage: true, reasoning_modes: ["auto"], default_max_tokens: 512, reasoning_control: null } },
  }],
  presets: {
    openai: { name: "OpenAI", adapter: "openai-compatible", base_url: "https://api.openai.com/v1" },
    ollama: { name: "Ollama", adapter: "openai-compatible", base_url: "http://localhost:11434/v1", models_url: "http://localhost:11434/v1/models" },
  },
  encryption: { available: true, migration_required: false },
}

function response(body: unknown) { return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } })) }
function errorResponse(status: number, message: string) { return Promise.resolve(new Response(JSON.stringify({ error: message }), { status, headers: { "Content-Type": "application/json" } })) }
function streamResponse(body: unknown) {
  const result = body as { text?: string | null; reasoning_text?: string | null; finish_reason?: string | null }
  const chunk = { choices: [{ delta: { content: result.text || "", reasoning_content: result.reasoning_text || "" }, finish_reason: result.finish_reason || "stop" }] }
  const payload = [
    `event: proxy.meta\ndata: ${JSON.stringify({ provider_id: "ollama", upstream_model: "qwen3:8b", model: "ollama::qwen3:8b", wait_ms: 0 })}`,
    `data: ${JSON.stringify(chunk)}`,
    `event: proxy.result\ndata: ${JSON.stringify(body)}`,
    "data: [DONE]",
  ].join("\n\n") + "\n\n"
  return Promise.resolve(new Response(payload, { status: 200, headers: { "Content-Type": "text/event-stream" } }))
}
const successfulTestResult = { ok: true, status_code: 200, wait_ms: 0, model: "qwen3:8b", upstream_model: "qwen3:8b", provider_id: "ollama", usage: { prompt_tokens: 8, completion_tokens: 16, total_tokens: 24 }, cost: { priced: true, input: 0.001, output: 0.004, total: 0.005 }, text: "## Result\n\nUse `range` with a fenced block:\n\n```python\ndef fibonacci(n):\n    return n\n```", response_payload: { choices: [] }, attempts: [] }
function mockFetch(testResult: unknown = successfulTestResult, holdStream = false) {
  vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input)
    if (url.includes("/api/auth/me")) return response({ user: { id: "user-1", email: "admin@example.com", role: "admin" } })
    if (url.includes("/api/access/users")) return response({ users: [] })
    if (url.includes("/api/access/keys")) return response({ keys: [] })
    if (url.includes("/api/access/audit")) return response({ events: [] })
    if (url.includes("/api/providers")) return response(providers)
    if (url.includes("/api/settings/routes")) return response({ routes: { qwen: [{ provider_id: "ollama", model: "qwen3:8b" }] } })
    if (url.includes("/api/settings/pricing")) return response({ prices: [{ provider_id: "ollama", model_id: "qwen3:8b", input_per_1m: 0.35, output_per_1m: 2.75 }] })
    if (url.includes("/api/settings")) return response(settings)
    if (url.includes("/charts")) return response({ range: "5m", bucket_seconds: 10, bucket_count: 1, window_seconds: 300, is_empty: true, ceiling_per_bucket: 1, status: { success: [0], throttled: [0], error: [0] }, queue_depth: [0], token_level: [1], generated_at: 0 })
    if (url.includes("/metrics")) return response({ range: "5m", bucket_seconds: 10, bucket_count: 1, window_seconds: 300, token_usage: { prompt: [0], completion: [0] }, latency: { queue_wait_avg: [0], model_response_avg: [0] }, percentiles: { p50: 0, p95: 0, p99: 0 }, tokens_histogram: { labels: ["0-100"], counts: [0] }, error_breakdown: { rate_limited: 0, server_error: 0, client_error: 0, timeout: 0, unknown: 0 }, generated_at: 0 })
    if (url.includes("/stats")) return response(stats)
    if (url.endsWith("/test/stream")) {
      if (!holdStream) return streamResponse(testResult)
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(`event: proxy.meta\ndata: ${JSON.stringify({ provider_id: "ollama", upstream_model: "qwen3:8b", model: "ollama::qwen3:8b", wait_ms: 0 })}\n\n`))
          init?.signal?.addEventListener("abort", () => controller.error(new DOMException("Aborted", "AbortError")), { once: true })
        },
      })
      return Promise.resolve(new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } }))
    }
    if (url.endsWith("/test")) return response(testResult)
    if (url.includes("/reset")) return response({ status: "reset" })
    if (url.includes("/api/requests")) return response({ requests: [], has_more: false, total_matching: 0 })
    return response({ requests: [], has_more: false })
  }))
}
function renderRoute(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<ThemeProvider><QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></QueryClientProvider></ThemeProvider>)
}

describe("dashboard application", () => {
  beforeEach(() => { localStorage.clear(); mockFetch() })
  afterEach(() => { cleanup(); vi.unstubAllGlobals() })

  it.each([["/", "Operational overview"], ["/live", "Live request feed"], ["/history", "Request history"], ["/test", "Test console"], ["/guide", "Guide"], ["/settings/providers", "Providers"], ["/settings/routing", "Model routing"], ["/settings/pricing", "Pricing"], ["/settings/storage", "Storage"], ["/settings/alerts", "Alerts"], ["/settings/access", "Access control"]])("routes %s to its page", async (path, heading) => {
    renderRoute(path)
    expect(await screen.findByRole("heading", { name: heading, level: 2 }, { timeout: 10_000 })).toBeInTheDocument()
  })

  it("shows the login surface when no dashboard session exists", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => String(input).includes("/api/auth/me") ? errorResponse(401, "Authentication is required") : response({})))
    renderRoute("/")
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument()
    expect(screen.getByText("Public registration is disabled. Access is managed by an Admin.")).toBeInTheDocument()
  })

  it("persists theme and sidebar choices", async () => {
    const user = userEvent.setup()
    renderRoute("/")
    await screen.findByRole("heading", { name: "Operational overview" })
    await user.click(screen.getByRole("button", { name: "Switch to dark mode" }))
    expect(localStorage.getItem("prismux-theme")).toBe("dark")
    expect(screen.getByRole("button", { name: "Switch to light mode" })).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }))
    expect(localStorage.getItem("prismux-sidebar")).toBe("collapsed")
  })

  it("explains empty overview ranges instead of rendering blank charts", async () => {
    renderRoute("/")
    expect(await screen.findByText("No requests in the last 5 min")).toBeInTheDocument()
    expect(screen.getByText("No token usage in the last 5 min")).toBeInTheDocument()
    expect(screen.getByText("No completed requests in the last 5 min")).toBeInTheDocument()
  })

  it("conceals a persisted secret and keeps structured settings editable", async () => {
    renderRoute("/settings/providers")
    expect(await screen.findByPlaceholderText("•••••••• · Leave blank to keep, type to replace")).toHaveAttribute("type", "password")
    expect(screen.getByText("PostgreSQL persistence active")).toBeInTheDocument()
    expect(screen.getByText(/A key is encrypted in PostgreSQL/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole("link", { name: "Model routing" }))
    expect(screen.getByRole("combobox", { name: /Model for qwen target 1/ })).toHaveTextContent("qwen3:8b")
    expect(screen.getByRole("button", { name: "Add route" })).toBeInTheDocument()
  })

  it("copies a one-time machine key on plain HTTP using the clipboard fallback", async () => {
    const secret = "prismux_live_abcde_this-is-a-long-machine-key-secret"
    const user = userEvent.setup()
    const execCommand = vi.fn(() => true)
    const setClipboardData = vi.fn()
    execCommand.mockImplementation(() => {
      const copyEvent = new Event("copy", { bubbles: true, cancelable: true })
      Object.defineProperty(copyEvent, "clipboardData", { value: { clearData: vi.fn(), setData: setClipboardData } })
      document.dispatchEvent(copyEvent)
      return true
    })
    Object.defineProperty(window, "isSecureContext", { configurable: true, value: false })
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined })
    Object.defineProperty(document, "execCommand", { configurable: true, value: execCommand })
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      if (url.includes("/api/auth/me")) return response({ user: { id: "user-1", email: "admin@example.com", role: "admin" } })
      if (url.includes("/api/access/users")) return response({ users: [] })
      if (url.includes("/api/access/audit")) return response({ events: [] })
      if (url.includes("/api/access/keys") && init?.method === "POST") return response({ key: { id: "key-1", name: "Hermes", prefix: "abcde", scopes: ["proxy:invoke"], expires_at: null, secret } })
      if (url.includes("/api/access/keys")) return response({ keys: [] })
      if (url.includes("/api/providers")) return response(providers)
      if (url.includes("/stats")) return response(stats)
      return response(settings)
    }))

    renderRoute("/settings/access")
    await user.type(await screen.findByLabelText("Key name"), "Hermes")
    await user.click(screen.getByRole("button", { name: "Create key" }))
    expect(await screen.findByRole("dialog")).toHaveTextContent("Copy this machine key now")
    expect(screen.getByLabelText("New machine API key")).toHaveTextContent(secret)
    await user.click(screen.getByRole("button", { name: "Copy key" }))
    expect(execCommand).toHaveBeenCalledWith("copy")
    expect(setClipboardData).toHaveBeenCalledWith("text/plain", secret)
    expect(screen.getByRole("button", { name: "Key copied" })).toHaveTextContent("Copied")
  })

  it("offers cached models in an explicit selector while keeping custom IDs editable", async () => {
    renderRoute("/settings/providers")
    const selector = await screen.findByRole("combobox", { name: "Discovered model" })
    expect(selector).toHaveValue("qwen3:8b")
    expect(screen.getByRole("textbox", { name: "Custom model ID" })).toHaveValue("qwen3:8b")
    expect(screen.getByRole("option", { name: "qwen3:8b" })).toBeInTheDocument()
    expect(screen.getByText(/\/ollama\/v1$/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Copy direct client URL" })).toBeInTheDocument()
  })

  it("scopes Test Console models to the selected configured provider", async () => {
    const user = userEvent.setup()
    renderRoute("/test")
    const providerSelector = await screen.findByRole("combobox", { name: "Provider" })
    const modelSelector = screen.getByRole("combobox", { name: "Model" })
    await waitFor(() => expect(providerSelector).toHaveValue("ollama"))
    expect(modelSelector).toHaveValue("ollama::qwen3:8b")
    expect(screen.queryByRole("option", { name: "gpt-5-mini" })).not.toBeInTheDocument()

    await user.selectOptions(providerSelector, "openai")
    expect(modelSelector).toHaveValue("openai::gpt-5-mini")
    expect(screen.getByRole("option", { name: "gpt-5" })).toBeInTheDocument()
    expect(screen.queryByRole("option", { name: "qwen3:8b" })).not.toBeInTheDocument()
  })

  it("uses streaming and model-aware generation controls", async () => {
    renderRoute("/test")
    await screen.findByRole("combobox", { name: "Provider" })
    expect(screen.queryByRole("heading", { name: "Response" })).not.toBeInTheDocument()
    expect(screen.getByRole("checkbox", { name: "Stream response" })).toBeChecked()
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Reasoning" })).toBeEnabled())
    expect(screen.getByRole("option", { name: "Off" })).toBeInTheDocument()
    expect(screen.getByRole("spinbutton", { name: "Output budget" })).toHaveValue(4096)
  })

  it("lets operators hide reasoning for models without a native reasoning switch", async () => {
    mockFetch({ ...successfulTestResult, text: "Final answer", reasoning_text: "Internal provider trace" })
    const user = userEvent.setup()
    renderRoute("/test")
    const providerSelector = await screen.findByRole("combobox", { name: "Provider" })
    await waitFor(() => expect(providerSelector).toHaveValue("ollama"))
    await user.selectOptions(providerSelector, "openai")
    const reasoningSelector = screen.getByRole("combobox", { name: "Reasoning" })
    expect(reasoningSelector).toBeEnabled()
    await user.selectOptions(reasoningSelector, "off")
    expect(screen.getByText("Trace hidden; the provider may still reason internally.")).toBeInTheDocument()
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Answer without showing the trace")
    await user.click(screen.getByRole("button", { name: "Send test request" }))
    expect(await screen.findByText("Final answer")).toBeInTheDocument()
    expect(screen.queryByText("Internal provider trace")).not.toBeInTheDocument()
  })

  it("uses a provider-scoped model picker for pricing", async () => {
    const user = userEvent.setup()
    renderRoute("/settings/pricing")
    const providerSelector = await screen.findByRole("combobox", { name: "Pricing provider 1" })
    const modelSelector = screen.getByRole("combobox", { name: "Pricing model 1" })
    expect(screen.getByRole("spinbutton", { name: "Input cost per 1M for row 1" })).toHaveValue(0.35)
    expect(screen.getByRole("spinbutton", { name: "Output cost per 1M for row 1" })).toHaveValue(2.75)
    expect(screen.getByText("Enter the provider's published input and output prices per 1 million tokens.")).toBeInTheDocument()
    expect(modelSelector).toHaveTextContent("qwen3:8b")
    await user.click(modelSelector)
    expect(screen.getByRole("option", { name: "Default fallback" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "qwen3:8b" })).toBeInTheDocument()
    expect(screen.queryByRole("option", { name: "gpt-5" })).not.toBeInTheDocument()

    await user.selectOptions(providerSelector, "openai")
    expect(modelSelector).toHaveTextContent("Default fallback")
    await user.click(modelSelector)
    expect(screen.getByRole("option", { name: "gpt-5" })).toBeInTheDocument()
    expect(screen.queryByRole("option", { name: "qwen3:8b" })).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Custom model ID…" }))
    expect(screen.getByRole("textbox", { name: "Custom pricing model 1" })).toBeInTheDocument()
  })

  it("renders one clear action in empty routing and pricing states", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const url = String(input)
      if (url.includes("/api/auth/me")) return response({ user: { id: "user-1", email: "admin@example.com", role: "admin" } })
      if (url.includes("/api/providers")) return response(providers)
      if (url.includes("/api/settings/routes")) return response({ routes: {} })
      if (url.includes("/api/settings/pricing")) return response({ prices: [] })
      if (url.includes("/stats")) return response(stats)
      return response(settings)
    }))

    const routing = renderRoute("/settings/routing")
    expect(await screen.findByText("No model routes")).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: "Add route" })).toHaveLength(1)
    routing.unmount()

    renderRoute("/settings/pricing")
    expect(await screen.findByText("No provider pricing")).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: "Add pricing" })).toHaveLength(1)
  })

  it("renders Test Console responses as safe structured Markdown", async () => {
    const user = userEvent.setup()
    renderRoute("/test")
    await screen.findByRole("combobox", { name: "Provider" })
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Show a Fibonacci function")
    await user.click(screen.getByRole("button", { name: "Send test request" }))
    expect(await screen.findByRole("heading", { name: "Result", level: 4 })).toBeInTheDocument()
    expect(screen.getByText("range", { selector: "code" })).toBeInTheDocument()
    expect(screen.getByLabelText("python code")).toHaveTextContent("def fibonacci")
    expect(screen.getByRole("button", { name: "Copy response" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Copy code" })).toBeInTheDocument()
    expect(screen.getByText("Input tokens")).toBeInTheDocument()
    expect(screen.getByText("Output tokens")).toBeInTheDocument()
    expect(screen.getByText("Total tokens")).toBeInTheDocument()
    expect(screen.getByText("Est. $0.00100")).toBeInTheDocument()
    expect(screen.getByText("Est. $0.00400")).toBeInTheDocument()
    expect(screen.getByText("Est. $0.00500")).toBeInTheDocument()
    expect(screen.queryByRole("spinbutton", { name: "Maximum tokens" })).not.toBeInTheDocument()
    expect(screen.getByText("Raw provider payload")).toBeInTheDocument()
  })

  it("shows provider reasoning when no final content was returned", async () => {
    mockFetch({ ...successfulTestResult, text: null, reasoning_text: "Still working through the answer", finish_reason: "length" })
    const user = userEvent.setup()
    renderRoute("/test")
    await screen.findByRole("combobox", { name: "Provider" })
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Explain the result")
    await user.click(screen.getByRole("button", { name: "Send test request" }))
    expect(await screen.findByText("The provider did not return a final answer.")).toBeInTheDocument()
    expect(screen.getByText("Still working through the answer")).toBeInTheDocument()
    expect(screen.getByText("Reasoning received")).toBeInTheDocument()
  })

  it("stops a streaming test while preserving the partial state", async () => {
    mockFetch(successfulTestResult, true)
    const user = userEvent.setup()
    renderRoute("/test")
    await screen.findByRole("combobox", { name: "Provider" })
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Keep generating")
    await user.click(screen.getByRole("button", { name: "Send test request" }))
    await user.click(await screen.findByRole("button", { name: "Stop generation" }))
    expect(await screen.findByText("Generation stopped. Partial output has been preserved.")).toBeInTheDocument()
  })

  it("keeps Storage focused on PostgreSQL retention settings", async () => {
    renderRoute("/settings/storage")
    await screen.findByRole("heading", { name: "Storage", level: 2 })
    expect(await screen.findByText("Queue history hours")).toBeInTheDocument()
    expect(screen.getByText("Payload days")).toBeInTheDocument()
    expect(screen.getByText("Statistics days")).toBeInTheDocument()
    expect(screen.queryByText(/seed path/i)).not.toBeInTheDocument()
  })

  it("requires confirmation before destructive reset", async () => {
    const user = userEvent.setup()
    renderRoute("/")
    await screen.findByRole("heading", { name: "Operational overview" })
    await user.click(screen.getByRole("button", { name: "Dashboard actions" }))
    await user.click(screen.getByText("Reset statistics"))
    expect(screen.getByRole("heading", { name: "Reset dashboard statistics?" })).toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalledWith("/reset", expect.anything())
  })
})
