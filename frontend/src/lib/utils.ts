import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatDuration(ms?: number | null) {
  if (ms == null) return "-"
  return ms >= 1000 ? `${(ms / 1000).toFixed(ms >= 10_000 ? 0 : 1)}s` : `${Math.round(ms)}ms`
}

export function formatNumber(value?: number | null, digits = 1) {
  if (value == null) return "-"
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(value)
}

export function formatCost(value?: number | null) {
  if (value == null) return "-"
  return value < 0.01 && value > 0 ? `$${value.toFixed(5)}` : `$${value.toFixed(2)}`
}

export function formatTime(value: string | number) {
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value.endsWith("Z") ? value : `${value}Z`)
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
}

export function formatUptime(seconds = 0) {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return days ? `${days}d ${hours}h` : hours ? `${hours}h ${minutes}m` : `${minutes}m`
}
