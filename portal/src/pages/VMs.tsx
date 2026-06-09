import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Play, Square, Trash2, ArrowRightLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Label } from '@/components/ui/label'
import { StatusBadge } from '@/components/StatusBadge'
import { useVMs, useStartVM, useStopVM, useDeleteVM, useMigrateVM } from '@/hooks/useVMs'
import { useNodes } from '@/hooks/useNodes'
import type { VM } from '@/types'

export function VMs() {
  const navigate = useNavigate()
  const { data: vms = [], isLoading, error } = useVMs()
  const { data: nodes = [] } = useNodes()
  const startVM = useStartVM()
  const stopVM = useStopVM()
  const deleteVM = useDeleteVM()
  const migrateVM = useMigrateVM()

  const [migrateTarget, setMigrateTarget] = useState<VM | null>(null)
  const [targetNodeId, setTargetNodeId] = useState('')

  if (isLoading) return <div className="text-[var(--muted-foreground)]">Loading VMs...</div>
  if (error) return <div className="text-red-400">Failed to load VMs: {error.message}</div>

  const handleMigrate = () => {
    if (!migrateTarget || !targetNodeId) return
    migrateVM.mutate(
      { id: migrateTarget.id, targetNodeId },
      { onSuccess: () => { setMigrateTarget(null); setTargetNodeId('') } },
    )
  }

  const onlineNodes = nodes.filter((n) => n.status === 'online')

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
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {vms.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={8} className="text-center text-[var(--muted-foreground)] py-10">
                    No VMs yet. <button onClick={() => navigate('/vms/create')} className="text-blue-400 hover:underline">Create one</button>
                  </TableCell>
                </TableRow>
              ) : (
                vms.map((vm) => (
                  <TableRow key={vm.id}>
                    <TableCell className="font-medium">{vm.name}</TableCell>
                    <TableCell><StatusBadge status={vm.status} /></TableCell>
                    <TableCell className="text-[var(--muted-foreground)] text-sm">
                      {nodes.find((n) => n.id === vm.node_id)?.hostname ?? '—'}
                    </TableCell>
                    <TableCell>{vm.cpu_cores} cores</TableCell>
                    <TableCell>{vm.ram_mb >= 1024 ? `${vm.ram_mb / 1024} GB` : `${vm.ram_mb} MB`}</TableCell>
                    <TableCell>{vm.disk_gb} GB</TableCell>
                    <TableCell className="text-[var(--muted-foreground)] text-sm">
                      {new Date(vm.created_at).toLocaleDateString()}
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        {vm.status === 'stopped' && (
                          <Button
                            variant="ghost"
                            size="icon"
                            title="Start"
                            onClick={() => startVM.mutate(vm.id)}
                          >
                            <Play className="h-3.5 w-3.5 text-green-400" />
                          </Button>
                        )}
                        {vm.status === 'running' && (
                          <Button
                            variant="ghost"
                            size="icon"
                            title="Stop"
                            onClick={() => stopVM.mutate(vm.id)}
                          >
                            <Square className="h-3.5 w-3.5 text-yellow-400" />
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="icon"
                          title="Migrate"
                          onClick={() => { setMigrateTarget(vm); setTargetNodeId('') }}
                        >
                          <ArrowRightLeft className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          title="Delete"
                          onClick={() => deleteVM.mutate(vm.id)}
                        >
                          <Trash2 className="h-3.5 w-3.5 text-red-400" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={!!migrateTarget} onOpenChange={(open) => !open && setMigrateTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Migrate {migrateTarget?.name}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <Label>Target Node</Label>
            <Select value={targetNodeId} onValueChange={setTargetNodeId}>
              <SelectTrigger>
                <SelectValue placeholder="Select target node..." />
              </SelectTrigger>
              <SelectContent>
                {onlineNodes
                  .filter((n) => n.id !== migrateTarget?.node_id)
                  .map((n) => (
                    <SelectItem key={n.id} value={n.id}>
                      {n.hostname} ({n.ip_address})
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setMigrateTarget(null)}>Cancel</Button>
            <Button onClick={handleMigrate} disabled={!targetNodeId || migrateVM.isPending}>
              {migrateVM.isPending ? 'Migrating...' : 'Migrate'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
