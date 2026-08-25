import { IconDownload, IconSearch, IconTrash, IconX } from "@tabler/icons-react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { getCoreRowModel, useReactTable } from "@tanstack/react-table"
import { useMemo, useState } from "react"
import { toast } from "sonner"
import { api, requestQuery, type RequestFilters } from "@/lib/api"
import type { RequestRow } from "@/lib/types"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { PageHeading } from "@/components/page-heading"
import { RequestTable } from "@/components/request-table"

interface FilterForm { start: string; end: string; provider_id: string; model: string; status: string; search: string }
const emptyForm: FilterForm = { start: "", end: "", provider_id: "", model: "", status: "", search: "" }

function normalize(form: FilterForm): RequestFilters {
  return {
    start_ts: form.start ? new Date(form.start).getTime() / 1000 : undefined,
    end_ts: form.end ? new Date(form.end).getTime() / 1000 : undefined,
    model: form.model || undefined,
    provider_id: form.provider_id || undefined,
    status: form.status || undefined,
    search: form.search.trim() || undefined,
  }
}

export function HistoryPage() {
  const [form, setForm] = useState<FilterForm>(emptyForm)
  const [filters, setFilters] = useState<RequestFilters>({})
  const [rows, setRows] = useState<RequestRow[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [total, setTotal] = useState(0)
  const [searched, setSearched] = useState(false)
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [deleteRow, setDeleteRow] = useState<RequestRow | null>(null)
  const [bulkCount, setBulkCount] = useState<number | null>(null)
  const queryClient = useQueryClient()
  const providers = useQuery({ queryKey: ["providers", "history"], queryFn: api.providers })
  const models = useMemo(() => [...new Set(rows.map((row) => row.model))].sort(), [rows])
  useReactTable({ data: rows, columns: [], getCoreRowModel: getCoreRowModel() })

  async function search(loadMore = false) {
    setLoading(true)
    try {
      const active = loadMore ? filters : normalize(form)
      const response = await api.searchRequests(active, loadMore ? rows.at(-1)?.id : undefined)
      setFilters(active)
      setRows((current) => loadMore ? [...current, ...response.requests] : response.requests)
      setHasMore(response.has_more)
      setTotal(response.total_matching)
      setSearched(true)
      if (!loadMore) setExpanded(new Set())
    } catch (error) { toast.error(error instanceof Error ? error.message : "Search failed") }
    finally { setLoading(false) }
  }
  const deleteOne = useMutation({ mutationFn: (id: number) => api.deleteRequest(id), onSuccess: (_, id) => { setRows((items) => items.filter((row) => row.id !== id)); setTotal((value) => Math.max(0, value - 1)); setDeleteRow(null); toast.success("Request deleted") }, onError: (error) => toast.error(error.message) })
  const bulkDelete = useMutation({ mutationFn: () => api.deleteMatching(filters), onSuccess: (result) => { setRows([]); setTotal(0); setHasMore(false); setBulkCount(null); queryClient.invalidateQueries(); toast.success(`${result.matched} requests deleted`) }, onError: (error) => toast.error(error.message) })
  const toggle = (id: number) => setExpanded((current) => { const next = new Set(current); next.has(id) ? next.delete(id) : next.add(id); return next })
  const setField = (key: keyof FilterForm, value: string) => setForm((current) => ({ ...current, [key]: value }))
  const exportHref = (format: "json" | "csv") => `/api/requests/export?${requestQuery(filters, { format })}`

  return <div className="page">
    <PageHeading title="Request history" description="Search retained statistics and payloads, export the current result set, or remove records with an explicit preview." />
    <div className="section mb-4"><div className="section-header"><div><h3>Filters</h3><p>Payload search applies only inside the detailed retention window.</p></div></div><div className="section-body">
      <div className="filters">
        <div className="field"><label htmlFor="history-from">From</label><Input id="history-from" type="datetime-local" value={form.start} onChange={(event) => setField("start", event.target.value)} /></div>
        <div className="field"><label htmlFor="history-to">To</label><Input id="history-to" type="datetime-local" value={form.end} onChange={(event) => setField("end", event.target.value)} /></div>
        <div className="field"><label htmlFor="history-provider">Provider</label><select id="history-provider" className="select" value={form.provider_id} onChange={(event) => setField("provider_id", event.target.value)}><option value="">All providers</option>{providers.data?.providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</select></div>
        <div className="field"><label htmlFor="history-model">Model</label><Input id="history-model" list="history-models" placeholder="All models" value={form.model} onChange={(event) => setField("model", event.target.value)} /><datalist id="history-models">{models.map((model) => <option key={model} value={model} />)}</datalist></div>
        <div className="field"><label htmlFor="history-status">Status</label><select id="history-status" className="select" value={form.status} onChange={(event) => setField("status", event.target.value)}><option value="">All statuses</option><option value="success">Success</option><option value="throttled">Throttled</option><option value="error">Error</option></select></div>
        <div className="field"><label htmlFor="history-search">Message content</label><Input id="history-search" placeholder="Search request text" value={form.search} onChange={(event) => setField("search", event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") search() }} /></div>
      </div>
      <div className="mt-4 flex gap-2"><Button onClick={() => search()} disabled={loading}><IconSearch size={16} />{loading ? "Searching" : "Search history"}</Button><Button variant="outline" onClick={() => { setForm(emptyForm); setFilters({}); setRows([]); setSearched(false); setTotal(0) }}><IconX size={16} />Clear</Button></div>
    </div></div>
    <div className="section">
      <div className="section-header"><div><h3>Request records</h3><p>{searched ? `Showing ${rows.length.toLocaleString()} of ${total.toLocaleString()} matching requests` : "Run a search to load records"}</p></div><div className="flex gap-2"><Button asChild size="sm" variant="outline"><a href={exportHref("json")}><IconDownload size={15} />JSON</a></Button><Button asChild size="sm" variant="outline"><a href={exportHref("csv")}><IconDownload size={15} />CSV</a></Button><Button size="sm" variant="destructive" disabled={!searched || total === 0} onClick={async () => { try { const preview = await api.previewDelete(filters); setBulkCount(preview.matched) } catch (error) { toast.error(error instanceof Error ? error.message : "Preview failed") } }}><IconTrash size={15} />Delete matching</Button></div></div>
      <RequestTable rows={rows} expanded={expanded} onToggle={toggle} onDelete={setDeleteRow} loading={loading && !rows.length} empty={searched ? "No matching requests" : "Run a search to see request history"} />
      {searched && <div className="flex justify-center border-t border-border p-3"><Button variant="outline" disabled={!hasMore || loading} onClick={() => search(true)}>{loading ? "Loading records" : hasMore ? "Load more" : "All matching records loaded"}</Button></div>}
    </div>

    <AlertDialog open={Boolean(deleteRow)} onOpenChange={(open) => !open && setDeleteRow(null)}><AlertDialogContent><AlertDialogTitle>Delete request #{deleteRow?.id}?</AlertDialogTitle><AlertDialogDescription className="text-sm text-muted-foreground">Its statistics and any retained request or response payload will be permanently removed.</AlertDialogDescription><div className="flex justify-end gap-2"><AlertDialogCancel asChild><Button variant="outline">Cancel</Button></AlertDialogCancel><AlertDialogAction asChild><Button variant="destructive" onClick={() => deleteRow && deleteOne.mutate(deleteRow.id)}>Delete request</Button></AlertDialogAction></div></AlertDialogContent></AlertDialog>
    <AlertDialog open={bulkCount != null} onOpenChange={(open) => !open && setBulkCount(null)}><AlertDialogContent><AlertDialogTitle>Delete {bulkCount?.toLocaleString()} matching requests?</AlertDialogTitle><AlertDialogDescription className="text-sm text-muted-foreground">This preview reflects the active filters. The matching statistics and retained payloads cannot be recovered.</AlertDialogDescription><div className="flex justify-end gap-2"><AlertDialogCancel asChild><Button variant="outline">Cancel</Button></AlertDialogCancel><AlertDialogAction asChild><Button variant="destructive" disabled={bulkDelete.isPending} onClick={() => bulkDelete.mutate()}>{bulkDelete.isPending ? "Deleting" : "Delete matching"}</Button></AlertDialogAction></div></AlertDialogContent></AlertDialog>
  </div>
}
