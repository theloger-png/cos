import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { StatusBadge } from '@/components/StatusBadge'
import { useNetworks, useCreateNetwork, useDeleteNetwork } from '@/hooks/useNetworks'

export function Networks() {
  const { data: networks = [], isLoading } = useNetworks()
  const createNetwork = useCreateNetwork()
  const deleteNetwork = useDeleteNetwork()

  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [vlanId, setVlanId] = useState('')
  const [cidr, setCidr] = useState('')
  const [gateway, setGateway] = useState('')

  const resetForm = () => { setName(''); setVlanId(''); setCidr(''); setGateway('') }

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault()
    createNetwork.mutate(
      {
        name,
        vlan_id: vlanId ? parseInt(vlanId) : undefined,
        cidr: cidr || undefined,
        gateway: gateway || undefined,
      },
      { onSuccess: () => { setOpen(false); resetForm() } },
    )
  }

  if (isLoading) return <div className="text-[var(--muted-foreground)]">Loading networks...</div>

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Networks</h2>
        <Button size="sm" onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4 mr-1" /> New Network
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">{networks.length} network{networks.length !== 1 ? 's' : ''}</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>VLAN ID</TableHead>
                <TableHead>CIDR</TableHead>
                <TableHead>Gateway</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {networks.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-[var(--muted-foreground)] py-10">
                    No networks yet
                  </TableCell>
                </TableRow>
              ) : (
                networks.map((net) => (
                  <TableRow key={net.id}>
                    <TableCell className="font-medium">{net.name}</TableCell>
                    <TableCell>{net.vlan_id ?? '—'}</TableCell>
                    <TableCell className="font-mono text-sm">{net.cidr ?? '—'}</TableCell>
                    <TableCell className="font-mono text-sm">{net.gateway ?? '—'}</TableCell>
                    <TableCell><StatusBadge status={net.status} /></TableCell>
                    <TableCell className="text-[var(--muted-foreground)] text-sm">
                      {new Date(net.created_at).toLocaleDateString()}
                    </TableCell>
                    <TableCell>
                      <Button variant="ghost" size="icon" onClick={() => deleteNetwork.mutate(net.id)}>
                        <Trash2 className="h-3.5 w-3.5 text-red-400" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) resetForm() }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Network</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleCreate}>
            <div className="space-y-4 py-2">
              <div className="space-y-2">
                <Label>Name *</Label>
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="tenant-vlan-100" required />
              </div>
              <div className="space-y-2">
                <Label>VLAN ID</Label>
                <Input type="number" min={1} max={4094} value={vlanId} onChange={(e) => setVlanId(e.target.value)} placeholder="100" />
              </div>
              <div className="space-y-2">
                <Label>CIDR</Label>
                <Input value={cidr} onChange={(e) => setCidr(e.target.value)} placeholder="10.100.0.0/24" />
              </div>
              <div className="space-y-2">
                <Label>Gateway</Label>
                <Input value={gateway} onChange={(e) => setGateway(e.target.value)} placeholder="10.100.0.1" />
              </div>
            </div>
            {createNetwork.error && (
              <p className="text-sm text-red-400 mb-3">{createNetwork.error.message}</p>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
              <Button type="submit" disabled={!name || createNetwork.isPending}>
                {createNetwork.isPending ? 'Creating...' : 'Create'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
