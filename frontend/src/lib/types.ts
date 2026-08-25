export type RequestStatus = "success" | "throttled" | "error"

export interface Usage {
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
}

export interface CostBreakdown {
  priced: boolean
  input: number | null
  output: number | null
  total: number | null
}

export interface RequestRow {
  id: number
  timestamp: string
  model: string
  upstream_model?: string
  wait_ms: number
  status: RequestStatus
  http_status?: number | null
  preview?: string
  request_payload?: Record<string, unknown> | null
  response_payload?: Record<string, unknown> | null
  payload_available?: boolean
  usage?: Usage | null
  model_response_ms?: number | null
  time_to_first_token_ms?: number | null
  input_cost?: number | null
  output_cost?: number | null
  estimated_cost?: number | null
  error_type?: string | null
  provider_id?: string | null
  route_alias?: string | null
  attempt_count?: number
}

export interface ProviderInfo { id: string; label: string }
export interface Alert { id: string; severity: "warning" | "critical"; message: string }
export interface ModelStats {
  total_requests: number
  requests_per_minute: number
  avg_wait_ms: number
  total_tokens: number
  total_cost: number
  avg_tokens_per_request: number
  avg_cost_per_request: number
  queued: number
}

export interface Stats {
  total_requests: number
  queued: number
  queued_last_hour: number
  token_level: number
  bucket_capacity: number
  requests_per_minute: number
  rate_limit_rpm: number
  avg_wait_ms: number
  avg_wait_recent_ms: number
  avg_wait_recent_n: number
  avg_response_recent_ms: number
  avg_time_to_first_token_ms: number
  throttled_last_hour: number
  last_outcome: RequestStatus | null
  payload_retention_days: number
  provider: ProviderInfo
  providers?: ProviderCapacity[]
  cost_today: number
  prompt_tokens_today: number
  completion_tokens_today: number
  cost_per_hour: Array<Record<string, number | string>>
  alerts: Alert[]
  by_model: Record<string, ModelStats>
  recent: RequestRow[]
  start_time: number
  uptime_seconds: number
}

export interface ProviderCapacity {
  id: string
  name: string
  preset: string
  enabled: boolean
  is_default: boolean
  health: string
  health_error: string
  last_checked_at: number | null
  rate_limit_rpm: number | null
  tokens_per_minute: number | null
  max_concurrency: number | null
  queued: number
  active: number
  request_level: number | null
  token_level: number | null
}

export interface ProviderConfig {
  id: string
  name: string
  preset: string
  adapter: "openai-compatible" | "anthropic"
  base_url: string
  models_url: string
  enabled: boolean
  is_default: boolean
  default_model: string
  rate_limit_rpm: number | null
  request_burst: number
  tokens_per_minute: number | null
  token_burst: number | null
  max_concurrency: number | null
  timeout_seconds: number
  anthropic_version: string
  headers: Record<string, string>
  api_key_configured: boolean
  health: string
  health_error: string
  last_checked_at: number | null
  models: string[]
  models_updated_at: string | null
  model_capabilities?: Record<string, ModelCapabilities>
  network_policy?: {
    status: "allowed" | "denied"
    error: string | null
    destinations: Array<{ field: string; normalized_url: string; hostname: string; port: number; addresses: string[]; classification: string }>
  }
}

export interface ModelCapabilities {
  streaming: boolean
  stream_usage?: boolean
  reasoning_modes: Array<"auto" | "on" | "off">
  default_max_tokens: number
  reasoning_control: string | null
}

export interface ProviderUpdate extends Omit<ProviderConfig, "api_key_configured" | "health" | "health_error" | "last_checked_at" | "models" | "models_updated_at"> {
  api_key: string
  clear_api_key: boolean
  secret_headers?: Record<string, string>
  clear_secret_headers?: boolean
}

export interface ProvidersResponse {
  providers: ProviderConfig[]
  presets: Record<string, { name: string; adapter: string; base_url: string; models_url?: string }>
  encryption: { available: boolean; migration_required: boolean; error?: string | null }
}

export interface RouteTarget { provider_id: string; model: string }
export interface RoutesResponse { routes: Record<string, RouteTarget[]> }
export interface PricingEntry { provider_id: string; model_id: string; input_per_1m: number; output_per_1m: number }
export interface PricingResponse { prices: PricingEntry[] }

export interface ChartsResponse {
  range: string
  bucket_seconds: number
  bucket_count: number
  window_seconds: number
  is_empty: boolean
  ceiling_per_bucket: number
  status: { labels: string[]; success: number[]; throttled: number[]; error: number[] }
  queue_depth: number[]
  token_level: number[]
  generated_at: number
}

export interface MetricsResponse {
  range: string
  bucket_seconds: number
  bucket_count: number
  window_seconds: number
  token_usage: Record<string, unknown>
  latency: Record<string, unknown>
  percentiles: Record<string, number | null>
  tokens_histogram: Record<string, unknown>
  error_breakdown: Array<Record<string, unknown>> | Record<string, number>
  generated_at: number
}

export interface RuntimeSettings {
  base_url: string
  models_url: string
  default_model: string
  rate_limit_rpm: number
  bucket_capacity: number
  retention_hours: number
  payload_retention_days: number
  stats_retention_days: number
  alert_queue_seconds: number
  alert_error_rate_pct: number
  alert_rpm_pct: number
}

export interface SettingsResponse {
  settings: RuntimeSettings
  configuration: {
    model_routes: Record<string, string>
    pricing_models: Record<string, { input_per_1m: number; output_per_1m: number }>
  }
  provider: ProviderInfo
  resolved_models_url: string
  api_key_configured: boolean
  deployment: { proxy_port: number; database: string }
}

export interface SettingsUpdate extends RuntimeSettings {
  api_key: string
  clear_api_key: boolean
  model_routes?: Record<string, string>
  pricing_models?: Record<string, { input_per_1m: number; output_per_1m: number }>
}

export interface TestResult {
  ok: boolean
  status_code?: number | null
  wait_ms: number
  error?: string | null
  text?: string | null
  reasoning_text?: string | null
  finish_reason?: string | null
  usage_estimated?: boolean
  usage?: Usage | null
  cost?: CostBreakdown | null
  response_payload?: Record<string, unknown> | null
  model: string
  upstream_model?: string
  provider_id?: string | null
  attempts?: Array<{ provider_id: string; upstream_model: string; status_code?: number | null; error_type?: string | null; fallback_reason?: string | null; wait_ms?: number; response_ms?: number | null }>
}

export type AccessRole = "admin" | "operator" | "viewer"
export interface AuthUser { id: string; email: string; role: AccessRole }
export interface AccessUser extends AuthUser { disabled: boolean; created_at?: string | null; last_sign_in_at?: string | null }
export interface ProxyApiKey {
  id: string
  name: string
  key_prefix: string
  scopes: string[]
  created_at: string
  expires_at: string | null
  revoked_at: string | null
  last_used_at: string | null
}
export interface AuditEvent {
  id: number
  occurred_at: string
  actor_type: string
  actor_id: string | null
  action: string
  target_type: string | null
  target_id: string | null
  outcome: "success" | "denied" | "failure"
  source_ip: string | null
  details: Record<string, unknown>
}
