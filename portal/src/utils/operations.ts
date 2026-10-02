import type { Operation } from '@/types'

export function formatClock(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '--:--:--'
  const time = d.toLocaleTimeString([], { hour12: false })
  const today = new Date()
  const sameDay =
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate()
  return sameDay ? time : `${d.toLocaleDateString()} ${time}`
}

/** "850 ms", "4.2 s", "3m 05s", "1h 02m" */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`
  const s = ms / 1000
  if (s < 10) return `${s.toFixed(1)} s`
  if (s < 60) return `${Math.round(s)} s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${String(Math.floor(s % 60)).padStart(2, '0')}s`
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`
}

/** Elapsed time of an operation; still-running ones count up to *now*. */
export function operationDuration(op: Operation, now: number): number {
  const start = new Date(op.started_at).getTime()
  const end = op.finished_at ? new Date(op.finished_at).getTime() : now
  return end - start
}
