import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, CheckCircle2, ChevronDown, ChevronRight, ChevronUp, Loader2, TerminalSquare } from 'lucide-react'
import { OperationProgress } from '@/components/OperationProgress'
import { useOperations } from '@/hooks/useOperations'
import { cn } from '@/lib/utils'
import { formatClock, formatDuration, operationDuration } from '@/utils/operations'
import type { Operation, OperationFilters, OperationStatus } from '@/types'

const COLLAPSED_ROWS = 5
const PAGE_SIZE = 200
const MAX_LIMIT = 500
const ROW_HEIGHT = 28
const HEADER_HEIGHT = 36
const MIN_EXPANDED = 200
const DEFAULT_EXPANDED = 320
const STORAGE_OPEN = 'cos-ops-open'
const STORAGE_HEIGHT = 'cos-ops-height'

const GRID = 'grid grid-cols-[9.5rem_8rem_minmax(0,1fr)_9rem_5.5rem_4.5rem] items-center gap-3 px-3'

const ACTION_GROUPS: { label: string; value: string }[] = [
  { label: 'All types', value: '' },
  { label: 'VMs', value: 'vm' },
  { label: 'Nodes', value: 'node' },
  { label: 'Networks', value: 'network' },
  { label: 'Tenants', value: 'tenant' },
  { label: 'Templates', value: 'template' },
  { label: 'Auth', value: 'auth' },
]

function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* storage unavailable: the console just won't remember its state */
  }
}

function StatusCell({ status }: { status: OperationStatus }) {
  if (status === 'running') {
    return (
      <span className="flex items-center gap-1 text-blue-400">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> running
      </span>
    )
  }
  if (status === 'failed') {
    return (
      <span className="flex items-center gap-1 text-red-400">
        <AlertCircle className="h-3.5 w-3.5" /> failed
      </span>
    )
  }
  return (
    <span className="flex items-center gap-1 text-green-400">
      <CheckCircle2 className="h-3.5 w-3.5" /> done
    </span>
  )
}

function OperationRow({
  op,
  now,
  expanded,
  onToggle,
}: {
  op: Operation
  now: number
  expanded: boolean
  onToggle: () => void
}) {
  return (
    <div className={cn('border-b border-[var(--border)]/50', expanded && 'bg-[var(--accent)]/40')}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        style={{ height: ROW_HEIGHT }}
        className={cn(GRID, 'w-full text-left text-xs hover:bg-[var(--accent)]/60')}
      >
        <span className="tabular-nums text-[var(--muted-foreground)]">{formatClock(op.started_at)}</span>
        <span className="truncate" title={op.user}>
          {op.user}
        </span>
        <span className="flex min-w-0 items-center gap-1">
          {expanded ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />}
          <span className="truncate" title={op.description}>
            {op.description}
          </span>
        </span>
        <OperationProgress operation={op} />
        <StatusCell status={op.status} />
        <span className="text-right tabular-nums text-[var(--muted-foreground)]">
          {formatDuration(operationDuration(op, now))}
        </span>
      </button>
      {expanded && (
        <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-3 gap-y-1 px-9 pb-2 pt-1 text-xs">
          <dt className="text-[var(--muted-foreground)]">Action</dt>
          <dd>{op.action}</dd>
          {(op.target_type || op.target_name) && (
            <>
              <dt className="text-[var(--muted-foreground)]">Target</dt>
              <dd>
                {op.target_type} {op.target_name}
                {op.target_id && <span className="ml-2 text-[var(--muted-foreground)]">{op.target_id}</span>}
              </dd>
            </>
          )}
          <dt className="text-[var(--muted-foreground)]">Started</dt>
          <dd>{new Date(op.started_at).toLocaleString()}</dd>
          <dt className="text-[var(--muted-foreground)]">Finished</dt>
          <dd>{op.finished_at ? new Date(op.finished_at).toLocaleString() : 'still running'}</dd>
          {op.progress !== null && (
            <>
              <dt className="text-[var(--muted-foreground)]">Progress</dt>
              <dd>{op.progress}%</dd>
            </>
          )}
          {op.message && (
            <>
              <dt className="text-[var(--muted-foreground)]">Details</dt>
              <dd className="whitespace-pre-wrap break-words">{op.message}</dd>
            </>
          )}
          {op.error && (
            <>
              <dt className="text-red-400">Error</dt>
              <dd className="whitespace-pre-wrap break-words text-red-400">{op.error}</dd>
            </>
          )}
        </dl>
      )}
    </div>
  )
}

const selectClass =
  'h-7 rounded-md border border-[var(--input)] bg-[var(--background)] px-2 text-xs text-[var(--foreground)] focus:outline-none focus:ring-1 focus:ring-[var(--ring)]'

/**
 * Bottom console showing recent and in-progress operations (who, what, when,
 * progress). Collapsed it is a fixed strip with the last few operations;
 * expanded it is a resizable panel with filters and "load more".
 */
export function OperationsConsole() {
  const [open, setOpen] = useState(() => readStorage(STORAGE_OPEN) === '1')
  const [height, setHeight] = useState(() => {
    const stored = Number(readStorage(STORAGE_HEIGHT))
    return stored >= MIN_EXPANDED ? stored : DEFAULT_EXPANDED
  })
  const [limit, setLimit] = useState(PAGE_SIZE)
  const [statusFilter, setStatusFilter] = useState<OperationStatus | ''>('')
  const [userFilter, setUserFilter] = useState('')
  const [actionFilter, setActionFilter] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const [userOptions, setUserOptions] = useState<string[]>([])

  const filters = useMemo<OperationFilters>(
    () => ({
      ...(statusFilter && { status: statusFilter }),
      ...(userFilter && { user: userFilter }),
      ...(actionFilter && { action: actionFilter }),
    }),
    [statusFilter, userFilter, actionFilter],
  )

  const { data: operations = [], isError } = useOperations(open ? limit : COLLAPSED_ROWS, open ? filters : {})
  const runningCount = operations.filter((o) => o.status === 'running').length
  const hasRunning = runningCount > 0

  // Tick once a second only while something is running, to animate its duration.
  useEffect(() => {
    if (!hasRunning) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [hasRunning])

  // Remember every user seen so far, so the user filter keeps offering the
  // others after one is selected (the server then only returns that user's rows).
  const unseen = [...new Set(operations.map((o) => o.user))].filter((u) => !userOptions.includes(u))
  if (unseen.length > 0) setUserOptions([...userOptions, ...unseen].sort())

  function toggleOpen() {
    setOpen((prev) => {
      writeStorage(STORAGE_OPEN, prev ? '0' : '1')
      return !prev
    })
  }

  function startResize(e: React.PointerEvent<HTMLDivElement>) {
    e.preventDefault()
    const startY = e.clientY
    const startHeight = height
    const max = Math.floor(window.innerHeight * 0.8)
    let latest = startHeight

    function onMove(ev: PointerEvent) {
      latest = Math.min(max, Math.max(MIN_EXPANDED, startHeight + (startY - ev.clientY)))
      setHeight(latest)
    }
    function onUp() {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      writeStorage(STORAGE_HEIGHT, String(latest))
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  const panelHeight = open ? height : HEADER_HEIGHT + COLLAPSED_ROWS * ROW_HEIGHT
  const visible = open ? operations : operations.slice(0, COLLAPSED_ROWS)

  return (
    <section
      aria-label="Operations console"
      style={{ height: panelHeight }}
      className="relative flex shrink-0 flex-col border-t border-[var(--border)] bg-[var(--card)] font-mono"
    >
      {open && (
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize operations console"
          onPointerDown={startResize}
          className="absolute -top-1 left-0 right-0 z-10 h-2 cursor-row-resize hover:bg-blue-500/40"
        />
      )}

      <header
        style={{ height: HEADER_HEIGHT }}
        className="flex shrink-0 items-center gap-3 border-b border-[var(--border)] px-3 text-xs"
      >
        <button type="button" onClick={toggleOpen} className="flex items-center gap-2 font-semibold" aria-expanded={open}>
          <TerminalSquare className="h-4 w-4" />
          Operations
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
        </button>
        {hasRunning && (
          <span className="flex items-center gap-1 rounded-full bg-blue-500/20 px-2 py-0.5 text-blue-400">
            <Loader2 className="h-3 w-3 animate-spin" /> {runningCount} running
          </span>
        )}
        {isError && <span className="text-red-400">cannot reach controller</span>}

        {open && (
          <div className="ml-auto flex items-center gap-2 font-sans">
            <select aria-label="Filter by type" className={selectClass} value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}>
              {ACTION_GROUPS.map((g) => (
                <option key={g.value} value={g.value}>
                  {g.label}
                </option>
              ))}
            </select>
            <select aria-label="Filter by user" className={selectClass} value={userFilter} onChange={(e) => setUserFilter(e.target.value)}>
              <option value="">All users</option>
              {userOptions.map((u) => (
                <option key={u} value={u}>
                  {u}
                </option>
              ))}
            </select>
            <select
              aria-label="Filter by status"
              className={selectClass}
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as OperationStatus | '')}
            >
              <option value="">All statuses</option>
              <option value="running">Running</option>
              <option value="success">Done</option>
              <option value="failed">Failed</option>
            </select>
          </div>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {visible.length === 0 ? (
          <p className="px-3 py-2 text-xs text-[var(--muted-foreground)]">No operations recorded yet.</p>
        ) : (
          visible.map((op) => (
            <OperationRow
              key={op.id}
              op={op}
              now={now}
              expanded={open && expandedId === op.id}
              onToggle={() => {
                if (!open) {
                  setOpen(true)
                  writeStorage(STORAGE_OPEN, '1')
                }
                setExpandedId((prev) => (prev === op.id ? null : op.id))
              }}
            />
          ))
        )}
        {open && operations.length >= limit && limit < MAX_LIMIT && (
          <button
            type="button"
            onClick={() => setLimit((l) => Math.min(MAX_LIMIT, l + 100))}
            className="w-full py-2 text-xs text-blue-400 hover:underline"
          >
            Load more
          </button>
        )}
      </div>
    </section>
  )
}
