import type { AccessRole, AccessUser, AuditEvent, AuthUser, ChartsResponse, MetricsResponse, PricingEntry, PricingResponse, ProviderConfig, ProvidersResponse, ProviderUpdate, ProxyApiKey, RequestRow, RoutesResponse, RouteTarget, SettingsResponse, SettingsUpdate, Stats, TestResult } from "@/lib/types"

export type TestStreamEvent =
  | { type: "meta"; data: Record<string, unknown> }
  | { type: "chunk"; data: Record<string, unknown> }
  | { type: "result"; data: TestResult }
  | { type: "done"; data: null }

export class ApiError extends Error {
  constructor(message: string, public status: number, public details?: unknown) {
    super(message)
  }
}

function cookie(name: string) {
  const prefix = `${encodeURIComponent(name)}=`
  const item = document.cookie.split("; ").find((value) => value.startsWith(prefix))
  return item ? decodeURIComponent(item.slice(prefix.length)) : ""
}

function requestHeaders(init?: RequestInit) {
  const method = (init?.method || "GET").toUpperCase()
  const csrf = !["GET", "HEAD", "OPTIONS"].includes(method) ? cookie("rlp_csrf") : ""
  return {
    ...(init?.body ? { "Content-Type": "application/json" } : {}),
    ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    ...init?.headers,
  }
}

async function readResponse(response: Response) {
  const contentType = response.headers.get("content-type") || ""
  return contentType.includes("application/json") ? response.json() : response.text()
}

async function request<T>(path: string, init?: RequestInit, mayRefresh = true): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: requestHeaders(init),
  })
  if (response.status === 401 && mayRefresh && !path.startsWith("/api/auth/")) {
    const refreshed = await fetch("/api/auth/refresh", {
      method: "POST",
      credentials: "same-origin",
      headers: requestHeaders({ method: "POST" }),
    })
    if (refreshed.ok) return request<T>(path, init, false)
  }
  const body = await readResponse(response)
  if (!response.ok) {
    const message = typeof body === "object" && body && "error" in body ? String(body.error) : `Request failed with HTTP ${response.status}`
    throw new ApiError(message, response.status, body)
  }
  return body as T
}

async function testStreamRequest(
  body: { model: string; content: string; max_tokens?: number; reasoning_mode?: "auto" | "on" | "off" },
  onEvent: (event: TestStreamEvent) => void,
  signal: AbortSignal,
  mayRefresh = true,
): Promise<void> {
  const response = await fetch("/test/stream", {
    method: "POST",
    credentials: "same-origin",
    headers: requestHeaders({ method: "POST", body: "{}" }),
    body: JSON.stringify(body),
    signal,
  })
  if (response.status === 401 && mayRefresh) {
    const refreshed = await fetch("/api/auth/refresh", {
      method: "POST",
      credentials: "same-origin",
      headers: requestHeaders({ method: "POST" }),
      signal,
    })
    if (refreshed.ok) return testStreamRequest(body, onEvent, signal, false)
  }
  if (!response.ok) {
    const error = await readResponse(response)
    const message = typeof error === "object" && error && "error" in error ? String(error.error) : `Request failed with HTTP ${response.status}`
    throw new ApiError(message, response.status, error)
  }
  if (!response.body) throw new ApiError("Provider stream did not include a response body", 502)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  function dispatch(block: string) {
    if (!block.trim()) return
    let eventName = "message"
    const data: string[] = []
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim()
      if (line.startsWith("data:")) data.push(line.slice(5).trimStart())
    }
    const payload = data.join("\n")
    if (!payload) return
    if (payload === "[DONE]") {
      onEvent({ type: "done", data: null })
      return
    }
    const parsed = JSON.parse(payload) as Record<string, unknown>
    if (eventName === "proxy.meta") onEvent({ type: "meta", data: parsed })
    else if (eventName === "proxy.result") onEvent({ type: "result", data: parsed as unknown as TestResult })
    else onEvent({ type: "chunk", data: parsed })
  }

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n")
    let boundary = buffer.indexOf("\n\n")
    while (boundary >= 0) {
      dispatch(buffer.slice(0, boundary))
      buffer = buffer.slice(boundary + 2)
      boundary = buffer.indexOf("\n\n")
    }
    if (done) break
  }
  if (buffer.trim()) dispatch(buffer)
}

export interface RequestFilters {
  start_ts?: number
  end_ts?: number
  model?: string
  status?: string
  search?: string
  provider_id?: string
}

export function requestQuery(filters: RequestFilters, extra: Record<string, string | number | boolean | undefined> = {}) {
  const params = new URLSearchParams()
  Object.entries({ ...filters, ...extra }).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value))
  })
  return params.toString()
}

export const api = {
  me: () => request<{ user: AuthUser }>("/api/auth/me", undefined, false),
  login: (body: { email: string; password: string }) => request<{ user: AuthUser }>("/api/auth/login", { method: "POST", body: JSON.stringify(body) }, false),
  logout: () => request<{ status: string }>("/api/auth/logout", { method: "POST" }, false),
  stats: () => request<Stats>("/stats"),
  charts: (range: string) => request<ChartsResponse>(`/charts?range=${encodeURIComponent(range)}`),
  metrics: (range: string) => request<MetricsResponse>(`/metrics?range=${encodeURIComponent(range)}`),
  recentRequests: (beforeId?: number, providerId?: string) => request<{ requests: RequestRow[]; has_more: boolean }>(`/requests?limit=50${beforeId ? `&before_id=${beforeId}` : ""}${providerId ? `&provider_id=${encodeURIComponent(providerId)}` : ""}`),
  searchRequests: (filters: RequestFilters, beforeId?: number) => request<{ requests: RequestRow[]; has_more: boolean; total_matching: number }>(`/api/requests?${requestQuery(filters, { limit: 50, before_id: beforeId })}`),
  deleteRequest: (id: number) => request<{ status: string; id: number }>(`/api/requests/${id}`, { method: "DELETE" }),
  previewDelete: (filters: RequestFilters) => request<{ matched: number; deleted: boolean }>(`/api/requests?${requestQuery(filters, { confirm: false })}`, { method: "DELETE" }),
  deleteMatching: (filters: RequestFilters) => request<{ matched: number; deleted: boolean }>(`/api/requests?${requestQuery(filters, { confirm: true })}`, { method: "DELETE" }),
  settings: () => request<SettingsResponse>("/api/settings"),
  updateSettings: (body: SettingsUpdate) => request<SettingsResponse>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  providers: () => request<ProvidersResponse>("/api/providers"),
  createProvider: (body: ProviderUpdate) => request<ProviderConfig>("/api/providers", { method: "POST", body: JSON.stringify(body) }),
  updateProvider: (id: string, body: ProviderUpdate) => request<ProviderConfig>(`/api/providers/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteProvider: (id: string) => request<{ status: string; id: string }>(`/api/providers/${encodeURIComponent(id)}`, { method: "DELETE" }),
  discoverProviderModels: (id: string, body?: Pick<ProviderUpdate, "base_url" | "models_url" | "adapter" | "api_key" | "headers" | "anthropic_version">) => request<{ provider_id: string; models: string[]; models_url: string; models_updated_at: string; health: string }>(`/api/providers/${encodeURIComponent(id)}/models`, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) }),
  checkProvider: (id: string) => request<{ provider_id: string; health: string; models_count: number }>(`/api/providers/${encodeURIComponent(id)}/health`, { method: "POST" }),
  providerTest: (id: string, body: { model: string; content: string; max_tokens?: number; reasoning_mode?: "auto" | "on" | "off" }) => request<TestResult>(`/api/providers/${encodeURIComponent(id)}/test`, { method: "POST", body: JSON.stringify(body) }),
  routes: () => request<RoutesResponse>("/api/settings/routes"),
  updateRoutes: (routes: Record<string, RouteTarget[]>) => request<RoutesResponse>("/api/settings/routes", { method: "PUT", body: JSON.stringify({ routes }) }),
  pricing: () => request<PricingResponse>("/api/settings/pricing"),
  updatePricing: (prices: PricingEntry[]) => request<PricingResponse>("/api/settings/pricing", { method: "PUT", body: JSON.stringify({ prices }) }),
  discoverModels: (body: { base_url: string; models_url: string; api_key: string }) => request<{ models: string[]; provider: { id: string; label: string }; models_url: string }>("/api/settings/models", { method: "POST", body: JSON.stringify(body) }),
  test: (body: { model: string; content: string; max_tokens?: number; reasoning_mode?: "auto" | "on" | "off" }) => request<TestResult>("/test", { method: "POST", body: JSON.stringify(body) }),
  testStream: testStreamRequest,
  reset: () => request<{ status: string }>("/reset", { method: "POST" }),
  accessUsers: () => request<{ users: AccessUser[] }>("/api/access/users"),
  createAccessUser: (body: { email: string; role: AccessRole; password?: string }) => request<{ user: AuthUser }>("/api/access/users", { method: "POST", body: JSON.stringify(body) }),
  updateAccessUser: (id: string, body: { email: string; role: AccessRole; disabled: boolean }) => request<{ user: AccessUser }>(`/api/access/users/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),
  accessKeys: () => request<{ keys: ProxyApiKey[] }>("/api/access/keys"),
  createAccessKey: (body: { name: string; expires_at?: string | null }) => request<{ key: ProxyApiKey & { prefix: string; secret: string } }>("/api/access/keys", { method: "POST", body: JSON.stringify(body) }),
  revokeAccessKey: (id: string) => request<{ status: string; id: string }>(`/api/access/keys/${encodeURIComponent(id)}`, { method: "DELETE" }),
  accessAudit: () => request<{ events: AuditEvent[] }>("/api/access/audit?limit=100"),
}
