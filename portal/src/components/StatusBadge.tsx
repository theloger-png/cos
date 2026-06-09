import { Badge } from '@/components/ui/badge'
import type { NetworkStatus, NodeStatus, VMStatus } from '@/types'

type AnyStatus = NodeStatus | VMStatus | NetworkStatus

const variantMap: Record<string, 'success' | 'error' | 'muted' | 'warning'> = {
  online: 'success',
  running: 'success',
  active: 'success',
  offline: 'error',
  error: 'error',
  stopped: 'muted',
  inactive: 'muted',
  starting: 'warning',
  stopping: 'warning',
  migrating: 'warning',
}

interface StatusBadgeProps {
  status: AnyStatus
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const variant = variantMap[status] ?? 'muted'
  return <Badge variant={variant}>{status}</Badge>
}
