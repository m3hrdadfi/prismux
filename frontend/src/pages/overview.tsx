import { IconAlertTriangle, IconArrowUpRight, IconBolt, IconClock, IconCoin, IconServer, IconStack2 } from "@tabler/icons-react"
import { useQuery } from "@tanstack/react-query"
import { useMemo, useState } from "react"
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts"
import { api } from "@/lib/api"
import { formatCost, formatDuration, formatNumber } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeading } from "@/components/page-heading"

const ranges = ["5m", "30m", "1h", "6h", "24h", "all"]
const rangeLabels: Record<string, string> = { "5m": "5 min", "30m": "30 min", "1h": "1 hour", "6h": "6 hours", "24h": "24 hours", all: "All time" }
const chartConfig = {
  success: { label: "Success", color: "var(--chart-1)" },
  throttled: { label: "Throttled", color: "var(--chart-5)" },
  error: { label: "Error", color: "var(--chart-4)" },
  queue: { label: "Queue", color: "var(--chart-2)" },
  tokens: { label: "Tokens", color: "var(--chart-1)" },
  prompt: { label: "Prompt", color: "var(--chart-1)" },
  completion: { label: "Completion", color: "var(--chart-3)" },
  wait: { label: "Queue wait", color: "var(--chart-2)" },
  response: { label: "Model response", color: "var(--chart-4)" },
}

export function OverviewPage() {
  const [range, setRange] = useState("5m")
  const stats = useQuery({ queryKey: ["stats", "overview"], queryFn: api.stats, refetchInterval: 1500 })
  const liveRange = range === "5m" || range === "30m"
  const charts = useQuery({ queryKey: ["charts", range], queryFn: () => api.charts(range), refetchInterval: liveRange ? 1500 : 30_000 })
  const metrics = useQuery({ queryKey: ["metrics", range], queryFn: () => api.metrics(range), refetchInterval: liveRange ? 10_000 : 30_000 })
  const chartData = useMemo(() => {
    if (!charts.data) return []
    return charts.data.status.success.map((success, index) => ({ bucket: index + 1, success, throttled: charts.data!.status.throttled[index], error: charts.data!.status.error[index], queue: charts.data!.queue_depth[index], tokens: charts.data!.token_level[index] }))
  }, [charts.data])
  const tokenData = useMemo(() => {
    const usage = metrics.data?.token_usage as { prompt?: number[]; completion?: number[] } | undefined
    return (usage?.prompt || []).map((prompt, index) => ({ bucket: index + 1, prompt, completion: usage?.completion?.[index] || 0 }))
  }, [metrics.data])
  const latencyData = useMemo(() => {
    const latency = metrics.data?.latency as { queue_wait_avg?: number[]; model_response_avg?: number[] } | undefined
    return (latency?.queue_wait_avg || []).map((wait, index) => ({ bucket: index + 1, wait, response: latency?.model_response_avg?.[index] || 0 }))
  }, [metrics.data])
  const histogramData = useMemo(() => {
    const histogram = metrics.data?.tokens_histogram as { labels?: string[]; counts?: number[] } | undefined
    return (histogram?.labels || []).map((label, index) => ({ label, count: histogram?.counts?.[index] || 0 }))
  }, [metrics.data])
  const hasCapacityData = chartData.some((item) => item.queue > 0 || item.tokens > 0)
  const hasTokenData = tokenData.some((item) => item.prompt > 0 || item.completion > 0)
  const hasLatencyData = latencyData.some((item) => item.wait > 0 || item.response > 0)
  const hasHistogramData = histogramData.some((item) => item.count > 0)
  const rpmPct = stats.data ? Math.min(100, (stats.data.requests_per_minute / stats.data.rate_limit_rpm) * 100) : 0
  const tokenPct = stats.data ? Math.min(100, (stats.data.token_level / stats.data.bucket_capacity) * 100) : 0
  const errors = metrics.data?.error_breakdown as Record<string, number> | undefined
  const recentFailures = errors ? Object.values(errors).reduce((sum, value) => sum + value, 0) : 0
  const defaultProvider = stats.data?.providers?.find((provider) => provider.is_default)
  const defaultHealth = stats.isError ? "offline" : defaultProvider?.health || "unknown"
  const defaultHealthState = defaultHealth === "healthy" ? "success" : defaultHealth === "degraded" || defaultHealth === "unknown" ? "warning" : "error"
  const defaultHealthNote = stats.isError ? "Dashboard cannot reach the proxy" : defaultHealth === "unknown" ? "Not checked since the proxy started" : defaultProvider?.health_error || `Last outcome: ${stats.data?.last_outcome || "No requests"}`

  return <div className="page">
    <PageHeading title="Operational overview" description="Live provider capacity first, followed by traffic health, latency, token use, and model-level activity." actions={<select className="select !w-36" value={range} onChange={(event) => setRange(event.target.value)} aria-label="Chart time range">{ranges.map((item) => <option key={item} value={item}>{rangeLabels[item]}</option>)}</select>} />
    {stats.data?.alerts.length ? <div className="alert-list">{stats.data.alerts.map((alert) => <div key={alert.id} className={`alert-banner ${alert.severity === "critical" ? "critical" : ""}`}><IconAlertTriangle size={17} /><span>{alert.message}</span></div>)}</div> : null}
    <div className="capacity-strip" aria-label="Provider capacity">
      <CapacityCell label="Provider health" value={stats.isError ? "Unavailable" : defaultProvider?.name || stats.data?.provider.label || "Checking"} note={defaultHealthNote} icon={<IconServer size={17} />} state={defaultHealthState} />
      <CapacityCell label="Token bucket" value={stats.data ? `${formatNumber(stats.data.token_level)} / ${formatNumber(stats.data.bucket_capacity)}` : undefined} note={`${formatNumber(tokenPct, 0)}% available`} icon={<IconBolt size={17} />} progress={tokenPct} />
      <CapacityCell label="Active queue" value={stats.data ? formatNumber(stats.data.queued, 0) : undefined} note={`${formatNumber(stats.data?.queued_last_hour, 0)} queued in the last hour`} icon={<IconStack2 size={17} />} />
      <CapacityCell label="RPM utilization" value={stats.data ? `${formatNumber(stats.data.requests_per_minute)} / ${formatNumber(stats.data.rate_limit_rpm, 0)}` : undefined} note={`${formatNumber(rpmPct, 0)}% of configured ceiling`} icon={<IconArrowUpRight size={17} />} progress={rpmPct} />
      <CapacityCell label="Recent failures" value={metrics.isError ? "Unavailable" : metrics.isLoading ? undefined : formatNumber(recentFailures, 0)} note={metrics.isError ? "Metrics endpoint unavailable" : `Across ${rangeLabels[range].toLowerCase()}`} icon={<IconAlertTriangle size={17} />} state={metrics.isError || recentFailures ? "error" : "success"} />
    </div>
    {stats.data?.providers?.length ? <div className="section mb-4 mt-4"><div className="section-header"><div><h3>Provider capacity</h3><p>Independent health, queue pressure, request budget, and concurrency.</p></div><Badge variant="outline">{stats.data.providers.filter((provider) => provider.enabled).length} enabled</Badge></div><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Provider</th><th>Health</th><th>Queued</th><th>Active</th><th>Request budget</th><th>RPM</th><th>TPM</th><th>Concurrency</th></tr></thead><tbody>{stats.data.providers.map((provider) => <tr key={provider.id}><td><div className="font-medium">{provider.name}</div><div className="font-mono text-[10px] text-muted-foreground">{provider.id}{provider.is_default ? " / default" : ""}</div></td><td><Badge title={provider.health_error || undefined} variant={provider.health === "healthy" ? "success" : ["offline", "auth_error"].includes(provider.health) ? "destructive" : provider.health === "degraded" ? "warning" : "secondary"}>{provider.enabled ? provider.health : "disabled"}</Badge></td><td className="mono">{formatNumber(provider.queued, 0)}</td><td className="mono">{formatNumber(provider.active, 0)}</td><td className="mono">{provider.request_level == null ? "Unlimited" : formatNumber(provider.request_level)}</td><td className="mono">{provider.rate_limit_rpm == null ? "Unlimited" : formatNumber(provider.rate_limit_rpm, 0)}</td><td className="mono">{provider.tokens_per_minute == null ? "Off" : formatNumber(provider.tokens_per_minute, 0)}</td><td className="mono">{provider.max_concurrency == null ? "Unlimited" : formatNumber(provider.max_concurrency, 0)}</td></tr>)}</tbody></table></div></div> : null}

    <div className="dashboard-grid">
      <ChartSection title="Request traffic" subtitle={`Status volume over ${rangeLabels[range].toLowerCase()}`} loading={charts.isLoading} error={charts.isError} empty={charts.data?.is_empty} emptyMessage={`No requests in the last ${rangeLabels[range].toLowerCase()}`}>
        <ChartContainer config={chartConfig}><AreaChart data={chartData} accessibilityLayer margin={{ left: -18, right: 8, top: 8 }}><CartesianGrid vertical={false} stroke="var(--border)" /><XAxis dataKey="bucket" hide /><YAxis allowDecimals={false} tickLine={false} axisLine={false} /><ChartTooltip content={<ChartTooltipContent />} /><Area type="monotone" dataKey="success" stackId="1" stroke="var(--color-success)" fill="var(--color-success)" fillOpacity={.22} /><Area type="monotone" dataKey="throttled" stackId="1" stroke="var(--color-throttled)" fill="var(--color-throttled)" fillOpacity={.45} /><Area type="monotone" dataKey="error" stackId="1" stroke="var(--color-error)" fill="var(--color-error)" fillOpacity={.5} /></AreaChart></ChartContainer>
      </ChartSection>
      <div className="stack">
        <div className="section"><div className="section-header"><div><h3>Current performance</h3><p>Rolling operational measurements</p></div><IconClock size={17} className="text-muted-foreground" /></div><div className="section-body grid grid-cols-2 gap-x-5 gap-y-5">
          <MiniMetric label="Average wait" value={formatDuration(stats.data?.avg_wait_recent_ms)} />
          <MiniMetric label="Model response" value={formatDuration(stats.data?.avg_response_recent_ms)} />
          <MiniMetric label="Time to first token" value={formatDuration(stats.data?.avg_time_to_first_token_ms)} />
          <MiniMetric label="Cost today" value={formatCost(stats.data?.cost_today)} />
        </div></div>
        <div className="section"><div className="section-header"><div><h3>Error analysis</h3><p>Failures in the selected range</p></div><Badge variant={recentFailures ? "destructive" : "success"}>{recentFailures ? `${recentFailures} failures` : "Clear"}</Badge></div><div className="section-body grid gap-2">
          {metrics.isError && <div className="error-box">Metrics could not be loaded. The dashboard will retry automatically.</div>}
          {!metrics.isError && errors && Object.entries(errors).map(([name, count]) => <div key={name} className="flex items-center justify-between gap-3 text-sm"><span className="capitalize text-muted-foreground">{name.replaceAll("_", " ")}</span><strong className="font-mono text-xs">{count}</strong></div>)}
          {metrics.isLoading && <Skeleton className="h-24" />}
        </div></div>
      </div>
    </div>

    <div className="chart-grid">
      <ChartSection title="Queue pressure" subtitle="Peak queue depth and available bucket tokens" loading={charts.isLoading} error={charts.isError} empty={Boolean(charts.data && !hasCapacityData)} emptyMessage={`No capacity samples in the last ${rangeLabels[range].toLowerCase()}`}><ChartContainer config={chartConfig}><LineChart data={chartData} accessibilityLayer margin={{ left: -18, right: 8, top: 8 }}><CartesianGrid vertical={false} stroke="var(--border)" /><XAxis dataKey="bucket" hide /><YAxis tickLine={false} axisLine={false} /><ChartTooltip content={<ChartTooltipContent />} /><Line type="monotone" dataKey="queue" stroke="var(--color-queue)" strokeWidth={2} dot={false} /><Line type="monotone" dataKey="tokens" stroke="var(--color-tokens)" strokeWidth={2} dot={false} /></LineChart></ChartContainer></ChartSection>
      <ChartSection title="Latency composition" subtitle="Queue wait compared with model response" loading={metrics.isLoading} error={metrics.isError} empty={Boolean(metrics.data && !hasLatencyData)} emptyMessage={`No latency samples in the last ${rangeLabels[range].toLowerCase()}`}><ChartContainer config={chartConfig}><LineChart data={latencyData} accessibilityLayer margin={{ left: -10, right: 8, top: 8 }}><CartesianGrid vertical={false} stroke="var(--border)" /><XAxis dataKey="bucket" hide /><YAxis tickLine={false} axisLine={false} /><ChartTooltip content={<ChartTooltipContent />} /><Line type="monotone" dataKey="wait" stroke="var(--color-wait)" strokeWidth={2} dot={false} /><Line type="monotone" dataKey="response" stroke="var(--color-response)" strokeWidth={2} dot={false} /></LineChart></ChartContainer></ChartSection>
      <ChartSection title="Token usage" subtitle="Prompt and completion tokens" loading={metrics.isLoading} error={metrics.isError} empty={Boolean(metrics.data && !hasTokenData)} emptyMessage={`No token usage in the last ${rangeLabels[range].toLowerCase()}`}><ChartContainer config={chartConfig}><AreaChart data={tokenData} accessibilityLayer margin={{ left: -10, right: 8, top: 8 }}><CartesianGrid vertical={false} stroke="var(--border)" /><XAxis dataKey="bucket" hide /><YAxis tickLine={false} axisLine={false} /><ChartTooltip content={<ChartTooltipContent />} /><Area type="monotone" dataKey="prompt" stackId="tokens" stroke="var(--color-prompt)" fill="var(--color-prompt)" fillOpacity={.26} /><Area type="monotone" dataKey="completion" stackId="tokens" stroke="var(--color-completion)" fill="var(--color-completion)" fillOpacity={.35} /></AreaChart></ChartContainer></ChartSection>
      <ChartSection title="Request size distribution" subtitle="Total tokens per completed request" loading={metrics.isLoading} error={metrics.isError} empty={Boolean(metrics.data && !hasHistogramData)} emptyMessage={`No completed requests in the last ${rangeLabels[range].toLowerCase()}`}><ChartContainer config={{ count: { label: "Requests", color: "var(--chart-3)" } }}><BarChart data={histogramData} accessibilityLayer margin={{ left: -18, right: 8, top: 8 }}><CartesianGrid vertical={false} stroke="var(--border)" /><XAxis dataKey="label" tickLine={false} axisLine={false} /><YAxis allowDecimals={false} tickLine={false} axisLine={false} /><ChartTooltip content={<ChartTooltipContent />} /><Bar dataKey="count" fill="var(--color-count)" radius={[4, 4, 0, 0]} /></BarChart></ChartContainer></ChartSection>
    </div>

    <div className="section mt-4"><div className="section-header"><div><h3>Model activity</h3><p>Traffic, queue, tokens, and estimated cost by client-facing model</p></div><IconCoin size={17} className="text-muted-foreground" /></div><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Model</th><th>Requests</th><th>Queued</th><th>Req/min</th><th>Average wait</th><th>Tokens</th><th>Est. cost</th></tr></thead><tbody>
      {stats.isLoading && <tr><td colSpan={7}><Skeleton className="h-24" /></td></tr>}
      {stats.data && !Object.keys(stats.data.by_model).length && <tr><td colSpan={7} className="table-empty">No model activity in the recent window</td></tr>}
      {stats.data && Object.entries(stats.data.by_model).sort(([, a], [, b]) => b.total_requests - a.total_requests).map(([model, item]) => <tr key={model}><td className="font-mono text-xs">{model}</td><td className="mono">{formatNumber(item.total_requests, 0)}</td><td className="mono">{formatNumber(item.queued, 0)}</td><td className="mono">{formatNumber(item.requests_per_minute)}</td><td className="mono">{formatDuration(item.avg_wait_ms)}</td><td className="mono">{formatNumber(item.total_tokens, 0)}</td><td className="mono">{formatCost(item.total_cost)}</td></tr>)}
    </tbody></table></div></div>
  </div>
}

function CapacityCell({ label, value, note, icon, progress, state }: { label: string; value?: string; note: string; icon: React.ReactNode; progress?: number; state?: "success" | "warning" | "error" }) {
  return <div className="capacity-cell"><div className="metric-label">{label}</div><div className="metric-inline"><div className="metric-value">{value ?? <Skeleton className="h-7 w-24" />}</div><span className="mt-2 text-muted-foreground">{icon}</span>{state && <span className={`semantic-dot mt-2 ${state}`} />}</div><div className="metric-note">{note}</div>{progress != null && <div className="progress-line"><span style={{ width: `${progress}%` }} /></div>}</div>
}
function MiniMetric({ label, value }: { label: string; value: string }) { return <div><div className="metric-label">{label}</div><div className="mt-1.5 font-mono text-lg font-semibold">{value}</div></div> }
function ChartSection({ title, subtitle, loading, error, empty, emptyMessage, children }: { title: string; subtitle: string; loading?: boolean; error?: boolean; empty?: boolean; emptyMessage?: string; children: React.ReactNode }) {
  return <div className="section"><div className="section-header"><div><h3>{title}</h3><p>{subtitle}</p></div></div><div className="section-body">
    {loading ? <Skeleton className="h-[240px]" /> : error ? <ChartState title="Chart unavailable" detail="The dashboard will retry automatically." tone="error" /> : empty ? <ChartState title="No data in this range" detail={emptyMessage || "Choose a longer time range to inspect earlier activity."} /> : children}
  </div></div>
}
function ChartState({ title, detail, tone }: { title: string; detail: string; tone?: "error" }) {
  return <div className={`flex h-[240px] flex-col items-center justify-center rounded-lg border border-dashed px-6 text-center ${tone === "error" ? "border-destructive/35 bg-destructive/5" : "border-border bg-muted/25"}`} role="status"><strong className={tone === "error" ? "text-destructive" : "text-foreground"}>{title}</strong><span className="mt-1 max-w-sm text-xs text-muted-foreground">{detail}</span></div>
}
