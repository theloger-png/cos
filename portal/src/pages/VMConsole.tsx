import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, RefreshCw } from 'lucide-react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { getConsoleTicket } from '@/api/vms'
import { useVMs } from '@/hooks/useVMs'
import { describeCloseCode } from '@/utils/consoleCloseCodes'

type ConnectionStatus = 'connecting' | 'connected' | 'disconnected'

const TERMINAL_THEME = {
  background: '#0a0a0a',
  foreground: '#e4e4e7',
  cursor: '#e4e4e7',
  selectionBackground: '#3f3f46',
  black: '#18181b',
  red: '#f87171',
  green: '#4ade80',
  yellow: '#facc15',
  blue: '#60a5fa',
  magenta: '#c084fc',
  cyan: '#22d3ee',
  white: '#e4e4e7',
  brightBlack: '#52525b',
  brightRed: '#fca5a5',
  brightGreen: '#86efac',
  brightYellow: '#fde047',
  brightBlue: '#93c5fd',
  brightMagenta: '#d8b4fe',
  brightCyan: '#67e8f9',
  brightWhite: '#fafafa',
}

const STATUS_VARIANT: Record<ConnectionStatus, 'success' | 'warning' | 'error'> = {
  connected: 'success',
  connecting: 'warning',
  disconnected: 'error',
}

/** Write a status line to the terminal in a dim/colored ANSI wrapper. */
function writeStatusLine(term: Terminal, text: string, ansiColor: string) {
  term.writeln(`\r\n\x1b[${ansiColor}m*** ${text} ***\x1b[0m`)
}

export function VMConsole() {
  const { id: vmId } = useParams<{ id: string }>()
  const { data: vms = [] } = useVMs()
  const vm = vms.find((v) => v.id === vmId)

  const [status, setStatus] = useState<ConnectionStatus>('connecting')
  const [statusDetail, setStatusDetail] = useState<string | null>(null)

  const containerRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitAddonRef = useRef<FitAddon | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  // Guards against a stale connect() attempt (superseded by a newer
  // Reconnect click, or the component unmounting) resurrecting a socket or
  // writing to an already-disposed terminal once its async work resolves.
  const attemptIdRef = useRef(0)

  const disconnectSocket = useCallback(() => {
    const ws = wsRef.current
    wsRef.current = null
    if (ws) {
      ws.onopen = null
      ws.onmessage = null
      ws.onclose = null
      ws.onerror = null
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close()
      }
    }
  }, [])

  const connect = useCallback(async () => {
    if (!vmId) return
    const myAttempt = ++attemptIdRef.current
    disconnectSocket()
    setStatus('connecting')
    setStatusDetail(null)

    let ticket: string
    try {
      const res = await getConsoleTicket(vmId)
      ticket = res.ticket
    } catch (err) {
      if (attemptIdRef.current !== myAttempt) return // superseded while awaiting
      const message = err instanceof Error ? err.message : 'Failed to request a console ticket'
      setStatus('disconnected')
      setStatusDetail(message)
      if (termRef.current) writeStatusLine(termRef.current, message, '31')
      return
    }
    if (attemptIdRef.current !== myAttempt) return // superseded while awaiting

    // Same-origin, relative to wherever this page was loaded from - the
    // ticket's REST call may go through a differently-configured baseURL,
    // but the WebSocket always goes through nginx's own /api/ proxy.
    const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${wsProtocol}://${window.location.host}/api/v1/vms/${vmId}/console?ticket=${encodeURIComponent(ticket)}`

    const ws = new WebSocket(url)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onopen = () => {
      if (wsRef.current !== ws) return
      setStatus('connected')
      setStatusDetail(null)
      if (termRef.current) {
        writeStatusLine(termRef.current, 'connected', '32')
        termRef.current.focus()
      }
    }
    ws.onmessage = (event) => {
      if (wsRef.current !== ws) return
      if (event.data instanceof ArrayBuffer) {
        termRef.current?.write(new Uint8Array(event.data))
      }
    }
    ws.onclose = (event) => {
      if (wsRef.current !== ws) return
      wsRef.current = null
      const message = describeCloseCode(event.code)
      setStatus('disconnected')
      setStatusDetail(message)
      if (termRef.current) writeStatusLine(termRef.current, message, '33')
    }
    ws.onerror = () => {
      // onclose fires right after with a real (or browser-synthesized) close
      // code - that's what drives the user-facing message. This is only for
      // debugging, the WebSocket error event itself carries no detail.
      console.error('VM console WebSocket error for VM', vmId)
    }
  }, [vmId, disconnectSocket])

  // Create the terminal once per vmId and tear it fully down on unmount /
  // vmId change. Reconnects (via connect()) reuse this same terminal so
  // scrollback survives a manual reconnect.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const term = new Terminal({
      convertEol: true,
      cursorBlink: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      fontSize: 14,
      theme: TERMINAL_THEME,
    })
    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    term.open(container)
    fitAddon.fit()
    termRef.current = term
    fitAddonRef.current = fitAddon

    term.onData((data) => {
      const ws = wsRef.current
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(new TextEncoder().encode(data))
      }
    })

    const onResize = () => fitAddonRef.current?.fit()
    window.addEventListener('resize', onResize)

    void connect()

    return () => {
      window.removeEventListener('resize', onResize)
      attemptIdRef.current += 1 // cancel any in-flight connect() attempt
      disconnectSocket()
      term.dispose()
      termRef.current = null
      fitAddonRef.current = null
    }
  }, [connect, disconnectSocket])

  return (
    <div className="flex flex-col h-full gap-4">
      <div className="flex items-center gap-3 shrink-0">
        <h2 className="text-xl font-semibold">{vm?.name ?? 'VM'} — Console</h2>
        <div className="flex items-center gap-2">
          <Badge variant={STATUS_VARIANT[status]}>{status}</Badge>
          {statusDetail && (
            <span className="text-xs text-[var(--muted-foreground)]">{statusDetail}</span>
          )}
        </div>
        <div className="flex-1" />
        <Button
          variant="outline"
          size="sm"
          onClick={() => void connect()}
          disabled={status === 'connecting'}
        >
          <RefreshCw className="h-3.5 w-3.5 mr-1" /> Reconnect
        </Button>
        <Link
          to="/vms"
          className="flex items-center gap-1 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back to VMs
        </Link>
      </div>

      <div className="flex-1 min-h-0 rounded-lg border border-[var(--border)] bg-black overflow-hidden">
        <div ref={containerRef} className="h-full w-full p-2" />
      </div>
    </div>
  )
}
