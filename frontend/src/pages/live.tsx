import { IconActivityHeartbeat, IconArrowUp, IconPlayerPause, IconPlayerPlay } from "@tabler/icons-react"
import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { useEffect, useMemo, useRef, useState } from "react"
import { api } from "@/lib/api"
import type { RequestRow } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { PageHeading } from "@/components/page-heading"
import { RequestTable } from "@/components/request-table"

export function LivePage() {
  const [paused, setPaused] = useState(false)
  const [providerId, setProviderId] = useState("")
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [pending, setPending] = useState<number[]>([])
  const known = useRef<Set<number>>(new Set())
  const stats = useQuery({
    queryKey: ["stats", "live"],
    queryFn: api.stats,
    refetchInterval: paused ? false : 1500,
    refetchIntervalInBackground: false,
  })
  const older = useInfiniteQuery({
    queryKey: ["requests", "live", "older", providerId],
    queryFn: ({ pageParam }) => api.recentRequests(pageParam, providerId || undefined),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.has_more ? page.requests.at(-1)?.id : undefined,
    enabled: false,
  })
  useEffect(() => {
    if (!stats.data) return
    const next = stats.data.recent.map((row) => row.id).filter((id) => !known.current.has(id))
    stats.data.recent.forEach((row) => known.current.add(row.id))
    if (known.current.size > stats.data.recent.length && next.length && window.scrollY > 40) setPending((items) => [...new Set([...next, ...items])])
  }, [stats.data])
  const rows = useMemo(() => {
    const byId = new Map<number, RequestRow>()
    stats.data?.recent.filter((row) => !providerId || row.provider_id === providerId).forEach((row) => byId.set(row.id, row))
    older.data?.pages.flatMap((page) => page.requests).forEach((row) => byId.set(row.id, row))
    return [...byId.values()].sort((a, b) => b.id - a.id)
  }, [stats.data, older.data, providerId])
  const toggle = (id: number) => setExpanded((current) => { const next = new Set(current); next.has(id) ? next.delete(id) : next.add(id); return next })

  return <div className="page">
    <PageHeading title="Live request feed" description="Polling is stable while this tab is visible. Pause the feed to inspect payloads without incoming rows shifting the table." actions={<div className="flex gap-2"><select className="select !w-44" value={providerId} onChange={(event) => setProviderId(event.target.value)} aria-label="Filter live feed by provider"><option value="">All providers</option>{stats.data?.providers?.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</select><Button variant={paused ? "default" : "outline"} onClick={() => setPaused((value) => !value)}>{paused ? <IconPlayerPlay size={16} /> : <IconPlayerPause size={16} />}{paused ? "Resume feed" : "Pause feed"}</Button></div>} />
    {pending.length > 0 && <div className="mb-3 flex justify-center"><Button size="sm" onClick={() => { setPending([]); window.scrollTo({ top: 0, behavior: "smooth" }) }}><IconArrowUp size={14} />{pending.length} new request{pending.length === 1 ? "" : "s"}</Button></div>}
    <div className="section">
      <div className="section-header"><div><h3 className="flex items-center gap-2"><IconActivityHeartbeat size={17} className={paused ? "text-muted-foreground" : "text-success"} />Recent requests</h3><p>{paused ? "Feed paused. Existing records remain available." : "Refreshes every 1.5 seconds while visible."}</p></div><span className="font-mono text-xs text-muted-foreground">{rows.length} loaded</span></div>
      <RequestTable rows={rows} expanded={expanded} onToggle={toggle} loading={stats.isLoading} />
      <div className="flex justify-center border-t border-border p-3"><Button variant="outline" onClick={() => older.fetchNextPage()} disabled={older.isFetchingNextPage || (older.data && !older.hasNextPage)}>{older.isFetchingNextPage ? "Loading records" : older.data && !older.hasNextPage ? "All records loaded" : "Load older requests"}</Button></div>
    </div>
  </div>
}
