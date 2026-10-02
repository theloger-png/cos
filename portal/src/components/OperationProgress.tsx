import { cn } from '@/lib/utils'
import type { Operation } from '@/types'

interface OperationProgressProps {
  operation: Pick<Operation, 'status' | 'progress'>
  className?: string
}

/** Thin progress bar: real percentage when known, sliding bar while indeterminate. */
export function OperationProgress({ operation, className }: OperationProgressProps) {
  const { status, progress } = operation
  const determinate = progress !== null
  const color =
    status === 'failed' ? 'bg-red-500' : status === 'success' ? 'bg-green-500' : 'bg-blue-500'

  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={determinate ? progress : undefined}
      className={cn('relative h-1.5 w-full overflow-hidden rounded-full bg-[var(--secondary)]', className)}
    >
      {determinate || status !== 'running' ? (
        <div
          className={cn('h-full rounded-full transition-all duration-500', color)}
          style={{ width: `${status === 'failed' && !determinate ? 100 : (progress ?? 100)}%` }}
        />
      ) : (
        <div className={cn('operation-indeterminate h-full w-1/4 rounded-full', color)} />
      )}
    </div>
  )
}
