import { cn } from '@/lib/utils'

interface ResourceBarProps {
  used: number
  total: number
  unit?: string
  showText?: boolean
  className?: string
}

function formatValue(value: number, unit: string): string {
  if (unit === 'MB') {
    return value >= 1024 ? `${(value / 1024).toFixed(1)} GB` : `${value} MB`
  }
  if (unit === 'GB') return `${value} GB`
  return `${value}${unit}`
}

export function ResourceBar({ used, total, unit = '', showText = true, className }: ResourceBarProps) {
  const pct = total > 0 ? Math.round((used / total) * 100) : 0
  const color =
    pct >= 90 ? 'bg-red-500' : pct >= 70 ? 'bg-yellow-500' : 'bg-green-500'

  return (
    <div className={cn('space-y-1', className)}>
      {showText && (
        <div className="flex justify-between text-xs text-[var(--muted-foreground)]">
          <span>{formatValue(used, unit)}</span>
          <span>{formatValue(total, unit)}</span>
        </div>
      )}
      <div className="relative h-2 w-full overflow-hidden rounded-full bg-[var(--secondary)]">
        <div
          className={cn('h-full transition-all', color)}
          style={{ width: `${pct}%` }}
        />
      </div>
      {showText && (
        <div className="text-xs text-[var(--muted-foreground)] text-right">{pct}%</div>
      )}
    </div>
  )
}
