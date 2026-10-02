import { useNavigate } from 'react-router-dom'
import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { StatusBadge } from '@/components/StatusBadge'
import { useVMs } from '@/hooks/useVMs'
import { useNodes } from '@/hooks/useNodes'
import { formatDate } from '@/utils/format'

export function VMs() {
  const navigate = useNavigate()
  const { data: vms = [], isLoading, error } = useVMs()
  const { data: nodes = [] } = useNodes()

  if (isLoading) return <div className="text-[var(--muted-foreground)]">Loading VMs...</div>
  if (error) return <div className="text-red-400">Failed to load VMs: {error.message}</div>

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Virtual Machines</h2>
        <Button size="sm" onClick={() => navigate('/vms/create')}>
          <Plus className="h-4 w-4 mr-1" /> Create VM
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">{vms.length} VM{vms.length !== 1 ? 's' : ''} total</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Node</TableHead>
                <TableHead>CPU</TableHead>
                <TableHead>RAM</TableHead>
                <TableHead>Disk</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {vms.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-[var(--muted-foreground)] py-10">
                    No VMs yet. <button onClick={() => navigate('/vms/create')} className="text-blue-400 hover:underline">Create one</button>
                  </TableCell>
                </TableRow>
              ) : (
                vms.map((vm) => (
                  <TableRow
                    key={vm.id}
                    className="cursor-pointer"
                    onClick={() => navigate(`/vms/${vm.id}`)}
                  >
                    <TableCell className="font-medium">{vm.name}</TableCell>
                    <TableCell><StatusBadge status={vm.status} /></TableCell>
                    <TableCell className="text-[var(--muted-foreground)] text-sm">
                      {nodes.find((n) => n.id === vm.node_id)?.hostname ?? '—'}
                    </TableCell>
                    <TableCell>{vm.cpu_cores} cores</TableCell>
                    <TableCell>{vm.ram_mb >= 1024 ? `${vm.ram_mb / 1024} GB` : `${vm.ram_mb} MB`}</TableCell>
                    <TableCell>{vm.disk_gb} GB</TableCell>
                    <TableCell className="text-[var(--muted-foreground)] text-sm">
                      {formatDate(vm.created_at)}
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
