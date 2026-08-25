import { IconChevronDown, IconChevronRight, IconFlask, IconTrash } from "@tabler/icons-react"
import type { ReactNode } from "react"
import type { RequestRow } from "@/lib/types"
import { formatCost, formatDuration, formatTime } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

export function StatusBadge({ row }: { row: RequestRow }) {
  const label = row.status === "error" ? (row.http_status ? `HTTP ${row.http_status}` : "Error") : row.status === "throttled" ? "Throttled" : "Success"
  const variant = row.status === "error" ? "destructive" : row.status === "throttled" ? "warning" : "success"
  return <Badge variant={variant}>{label}</Badge>
}

export function RequestTable({ rows, expanded, onToggle, onDelete, loading, empty = "No requests yet", actionHeader }: {
  rows: RequestRow[]
  expanded: Set<number>
  onToggle: (id: number) => void
  onDelete?: (row: RequestRow) => void
  loading?: boolean
  empty?: string
  actionHeader?: ReactNode
}) {
  const columnCount = onDelete ? 11 : 10
  return <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Time</th><th>Provider</th><th>Model</th><th>Preview</th><th>Wait</th><th>Latency</th><th>Input cost</th><th>Output cost</th><th>Total cost</th><th>Status</th>{onDelete && <th>{actionHeader}</th>}</tr></thead><tbody>
    {loading && Array.from({ length: 6 }, (_, index) => <tr key={index}>{Array.from({ length: columnCount }, (_, cell) => <td key={cell}><Skeleton className="h-4 w-full max-w-28" /></td>)}</tr>)}
    {!loading && !rows.length && <tr><td className="table-empty" colSpan={columnCount}>{empty}</td></tr>}
    {!loading && rows.map((row) => {
      const isOpen = expanded.has(row.id)
      const isTest = Boolean(row.request_payload?._test)
      return <RequestRows key={row.id} row={row} isOpen={isOpen} onToggle={onToggle} onDelete={onDelete} isTest={isTest} />
    })}
  </tbody></table></div>
}

function RequestRows({ row, isOpen, onToggle, onDelete, isTest }: { row: RequestRow; isOpen: boolean; onToggle: (id: number) => void; onDelete?: (row: RequestRow) => void; isTest: boolean }) {
  return <>
    <tr data-clickable="true" onClick={() => onToggle(row.id)}>
      <td className="mono"><span className="inline-flex items-center gap-1">{isOpen ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}{formatTime(row.timestamp)}</span></td>
      <td className="w-36 min-w-36 max-w-36">
        <Badge className="max-w-full whitespace-nowrap" variant="outline" title={row.provider_id || "legacy"}>
          <span className="block truncate">{row.provider_id || "legacy"}</span>
        </Badge>
      </td>
      <td><span className="inline-flex items-center gap-2"><span className="max-w-56 truncate font-mono text-xs">{row.model}</span>{isTest && <Badge variant="secondary"><IconFlask size={11} />Test</Badge>}</span></td>
      <td className="max-w-80 truncate text-muted-foreground">{row.preview || "-"}</td>
      <td className="mono">{formatDuration(row.wait_ms)}</td>
      <td className="mono">{formatDuration(row.model_response_ms)}</td>
      <td className="mono whitespace-nowrap">{formatCost(row.input_cost)}</td>
      <td className="mono whitespace-nowrap">{formatCost(row.output_cost)}</td>
      <td className="mono whitespace-nowrap font-semibold">{formatCost(row.estimated_cost)}</td>
      <td><StatusBadge row={row} /></td>
      {onDelete && <td><Button size="icon" variant="ghost" aria-label={`Delete request ${row.id}`} onClick={(event) => { event.stopPropagation(); onDelete(row) }}><IconTrash size={15} /></Button></td>}
    </tr>
    {isOpen && <tr><td colSpan={onDelete ? 11 : 10}><div className="detail-panel">
      <div><div className="mb-2 text-xs font-semibold">Request to <span className="font-mono">{row.provider_id ? `${row.provider_id}::` : ""}{row.upstream_model || row.model}</span>{(row.attempt_count || 1) > 1 && <Badge className="ml-2" variant="warning">{row.attempt_count} attempts</Badge>}</div>{row.payload_available === false ? <div className="error-box">Payload is past the configured retention window.</div> : <pre className="json-block">{JSON.stringify(row.request_payload, null, 2) || "No request payload"}</pre>}</div>
      <div><div className="mb-2 flex items-center justify-between gap-2 text-xs font-semibold"><span>Response payload</span><span className="font-mono text-muted-foreground">{row.usage?.total_tokens ?? 0} tokens / {formatCost(row.estimated_cost)}</span></div>{row.payload_available === false ? <div className="error-box">Payload is past the configured retention window.</div> : <pre className="json-block">{JSON.stringify(row.response_payload, null, 2) || "No response payload"}</pre>}</div>
    </div></td></tr>}
  </>
}
