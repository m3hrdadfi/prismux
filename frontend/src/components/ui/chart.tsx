import * as React from "react"
import { ResponsiveContainer, Tooltip } from "recharts"
import { cn } from "@/lib/utils"

export type ChartConfig = Record<string, { label: string; color: string }>
export function ChartContainer({ config, className, children }: { config: ChartConfig; className?: string; children: React.ReactElement }) {
  const style = Object.fromEntries(Object.entries(config).map(([key, value]) => [`--color-${key}`, value.color])) as React.CSSProperties
  return <div className={cn("h-[240px] min-h-[240px] w-full text-xs", className)} style={style}><ResponsiveContainer width="100%" height="100%">{children}</ResponsiveContainer></div>
}
export const ChartTooltip = Tooltip
export function ChartTooltipContent({ active, payload, label }: { active?: boolean; payload?: Array<{ name?: string; value?: number; color?: string }>; label?: string }) {
  if (!active || !payload?.length) return null
  return <div className="rounded-[8px] border border-border bg-popover p-2.5 text-popover-foreground shadow-lg"><div className="mb-1 text-xs text-muted-foreground">{label}</div>{payload.map((item) => <div key={item.name} className="flex min-w-32 justify-between gap-4 font-mono text-xs"><span>{item.name}</span><strong>{item.value}</strong></div>)}</div>
}
