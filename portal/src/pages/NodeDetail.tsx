import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { StatusBadge } from '@/components/StatusBadge'
import { ResourceBar } from '@/components/ResourceBar'
import { useNode } from '@/hooks/useNodes'
import { useVMs } from '@/hooks/useVMs'
import { formatDate } from '@/utils/format'

export function NodeDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { data: node, isLoading, error } = useNode(id ?? '')
  const { data: allVMs = [] } = useVMs()

  const nodeVMs = allVMs.filter((vm) => vm.node_id === id)

  if (isLoading) return <div className="text-[var(--muted-foreground)]">Loading node...</div>
  if (error || !node) return <div className="text-red-400">Node not found</div>

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate('/nodes')}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <h2 className="text-xl font-semibold">{node.hostname}</h2>
        <StatusBadge status={node.status} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card>
          <CardHeader><CardTitle className="text-sm">Node Info</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-[var(--muted-foreground)]">ID</span>
              <span className="font-mono text-xs">{node.id}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--muted-foreground)]">IP Address</span>
              <span className="font-mono">{node.ip_address}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--muted-foreground)]">Last Heartbeat</span>
              <span>{node.last_heartbeat ? formatDate(node.last_heartbeat) : 'Never'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--muted-foreground)]">Registered</span>
              <span>{formatDate(node.created_at)}</span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-sm">Resources</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <div>
              <div className="flex justify-between text-xs text-[var(--muted-foreground)] mb-1">
                <span>CPU</span>
                <span>{node.cpu_used}/{node.cpu_total} cores</span>
              </div>
              <ResourceBar used={node.cpu_used} total={node.cpu_total} unit="cores" showText={false} />
            </div>
            <div>
              <div className="flex justify-between text-xs text-[var(--muted-foreground)] mb-1">
                <span>RAM</span>
                <span>{Math.round(node.ram_used_mb / 1024)}/{Math.round(node.ram_total_mb / 1024)} GB</span>
              </div>
              <ResourceBar used={node.ram_used_mb} total={node.ram_total_mb} unit="MB" showText={false} />
            </div>
            <div>
              <div className="flex justify-between text-xs text-[var(--muted-foreground)] mb-1">
                <span>Disk</span>
                <span>{node.disk_used_gb}/{node.disk_total_gb} GB</span>
              </div>
              <ResourceBar used={node.disk_used_gb} total={node.disk_total_gb} unit="GB" showText={false} />
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Virtual Machines on this node ({nodeVMs.length})</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>CPU</TableHead>
                <TableHead>RAM</TableHead>
                <TableHead>Disk</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {nodeVMs.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center text-[var(--muted-foreground)] py-8">
                    No VMs on this node
                  </TableCell>
                </TableRow>
              ) : (
                nodeVMs.map((vm) => (
                  <TableRow key={vm.id}>
                    <TableCell className="font-medium">{vm.name}</TableCell>
                    <TableCell><StatusBadge status={vm.status} /></TableCell>
                    <TableCell>{vm.cpu_cores} cores</TableCell>
                    <TableCell>{vm.ram_mb >= 1024 ? `${vm.ram_mb / 1024} GB` : `${vm.ram_mb} MB`}</TableCell>
                    <TableCell>{vm.disk_gb} GB</TableCell>
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
