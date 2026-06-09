import { useNavigate } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { StatusBadge } from '@/components/StatusBadge'
import { ResourceBar } from '@/components/ResourceBar'
import { useNodes } from '@/hooks/useNodes'

function formatHeartbeat(ts: string | null): string {
  if (!ts) return 'Never'
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  return `${Math.floor(diff / 3600)}h ago`
}

export function Nodes() {
  const { data: nodes = [], isLoading, error } = useNodes()
  const navigate = useNavigate()

  if (isLoading) {
    return <div className="text-[var(--muted-foreground)]">Loading nodes...</div>
  }

  if (error) {
    return <div className="text-red-400">Failed to load nodes: {error.message}</div>
  }

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">Nodes</h2>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">{nodes.length} node{nodes.length !== 1 ? 's' : ''} registered</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Hostname</TableHead>
                <TableHead>IP Address</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>CPU</TableHead>
                <TableHead>RAM</TableHead>
                <TableHead>Disk</TableHead>
                <TableHead>Last Heartbeat</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {nodes.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-[var(--muted-foreground)] py-10">
                    No nodes registered
                  </TableCell>
                </TableRow>
              ) : (
                nodes.map((node) => (
                  <TableRow
                    key={node.id}
                    className="cursor-pointer"
                    onClick={() => navigate(`/nodes/${node.id}`)}
                  >
                    <TableCell className="font-medium text-blue-400 hover:underline">
                      {node.hostname}
                    </TableCell>
                    <TableCell className="font-mono text-sm">{node.ip_address}</TableCell>
                    <TableCell><StatusBadge status={node.status} /></TableCell>
                    <TableCell>
                      <div className="w-28">
                        <ResourceBar used={node.cpu_used} total={node.cpu_total} unit="cores" showText={false} />
                        <div className="text-xs text-[var(--muted-foreground)] mt-1">
                          {node.cpu_used}/{node.cpu_total}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="w-32">
                        <ResourceBar used={node.ram_used_mb} total={node.ram_total_mb} unit="MB" showText={false} />
                        <div className="text-xs text-[var(--muted-foreground)] mt-1">
                          {Math.round(node.ram_used_mb / 1024)}/{Math.round(node.ram_total_mb / 1024)} GB
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="w-32">
                        <ResourceBar used={node.disk_used_gb} total={node.disk_total_gb} unit="GB" showText={false} />
                        <div className="text-xs text-[var(--muted-foreground)] mt-1">
                          {node.disk_used_gb}/{node.disk_total_gb} GB
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="text-[var(--muted-foreground)] text-sm">
                      {formatHeartbeat(node.last_heartbeat)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
