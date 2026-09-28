/**
 * Maps a VM console WebSocket close code to a short, human-readable reason.
 *
 * Matches controller/api/routers/console.py and agent/ws_server.py: 4404,
 * 4409 and 4500 are set by the agent and mirrored verbatim by the
 * controller's relay; 4401 and 4503 are set by the controller itself.
 * 1000 and 1006 are standard WebSocket codes (normal closure, and the
 * browser-synthesized code for a handshake/connection failure that never
 * produced a real close frame). Kept as a small pure function, separate
 * from VMConsole.tsx, so it stays easy to unit test on its own.
 */
const CLOSE_CODE_MESSAGES: Record<number, string> = {
  1000: 'Connection closed.',
  1006: 'Connection lost (network error or server unreachable).',
  1011: 'Internal error.',
  4401: 'Ticket expired or invalid. Reconnect to get a new one.',
  4404: 'VM not found.',
  4409: 'VM is not running.',
  4500: 'Console unavailable on this VM.',
  4503: 'Node is offline.',
}

export function describeCloseCode(code: number): string {
  return CLOSE_CODE_MESSAGES[code] ?? `Connection closed (code ${code}).`
}
