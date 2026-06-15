import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { AlertTriangle, ArrowLeft, HardDrive, Network, Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { useVMHardware, useApplyVMHardware } from '@/hooks/useVMs'
import { useNetworks } from '@/hooks/useNetworks'
import type { NICFailure, VMHardwareChanges } from '@/types'

export function VMHardware() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const { data: hardware, isLoading, error } = useVMHardware(id ?? '')
  const { data: allNetworks = [] } = useNetworks()
  const applyHardware = useApplyVMHardware(id ?? '')

  // Editable CPU / RAM (mirror current hardware, user edits in place)
  const [vcpu, setVcpu] = useState(0)
  const [memoryMb, setMemoryMb] = useState(0)

  // Pending additions / removals
  const [disksToAdd, setDisksToAdd] = useState<{ size_gb: number }[]>([])
  const [nicsToAdd, setNicsToAdd] = useState<{ network_id: string; network_name: string }[]>([])
  const [nicsToRemove, setNicsToRemove] = useState<{ target: string }[]>([])

  // Inline forms
  const [newDiskSizeGb, setNewDiskSizeGb] = useState('')
  const [newNicNetworkId, setNewNicNetworkId] = useState('none')

  // Confirmation dialog
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [nicFailureWarnings, setNicFailureWarnings] = useState<NICFailure[]>([])

  // Seed local state when hardware loads
  useEffect(() => {
    if (hardware) {
      setVcpu(hardware.vcpu)
      setMemoryMb(hardware.memory_mb)
    }
  }, [hardware])

  if (isLoading) return <div className="text-[var(--muted-foreground)]">Loading hardware configuration…</div>
  if (error || !hardware) return <div className="text-red-400">Failed to load hardware: {(error as Error)?.message}</div>

  // Derived state
  const vcpuChanged = vcpu !== hardware.vcpu
  const memoryChanged = memoryMb !== hardware.memory_mb
  const hasChanges = vcpuChanged || memoryChanged || disksToAdd.length > 0 || nicsToAdd.length > 0 || nicsToRemove.length > 0
  const needsReboot = vcpuChanged || memoryChanged || disksToAdd.length > 0

  const pendingSummaryParts: string[] = []
  if (vcpuChanged) pendingSummaryParts.push(`CPU: ${hardware.vcpu} → ${vcpu}`)
  if (memoryChanged) pendingSummaryParts.push(`RAM: ${hardware.memory_mb} MB → ${memoryMb} MB`)
  disksToAdd.forEach((d) => pendingSummaryParts.push(`+disk ${d.size_gb} GB`))
  nicsToAdd.forEach((n) => pendingSummaryParts.push(`+NIC (${n.network_name})`))
  nicsToRemove.forEach((n) => pendingSummaryParts.push(`-NIC ${n.target}`))

  const handleAddDisk = () => {
    const gb = parseFloat(newDiskSizeGb)
    if (!gb || gb <= 0) return
    setDisksToAdd((prev) => [...prev, { size_gb: gb }])
    setNewDiskSizeGb('')
  }

  const handleAddNic = () => {
    if (newNicNetworkId === 'none') return
    const net = allNetworks.find((n) => n.id === newNicNetworkId)
    if (!net) return
    setNicsToAdd((prev) => [...prev, { network_id: net.id, network_name: net.name }])
    setNewNicNetworkId('none')
  }

  const handleRemoveNic = (target: string) => {
    // Don't add duplicates
    if (nicsToRemove.some((n) => n.target === target)) return
    setNicsToRemove((prev) => [...prev, { target }])
  }

  const handleApply = () => {
    setApplyError(null)
    const changes: VMHardwareChanges = {
      add_disks: disksToAdd,
      add_nics: nicsToAdd.map((n) => ({ network_id: n.network_id })),
      remove_nics: nicsToRemove,
    }
    if (vcpuChanged) changes.vcpu = vcpu
    if (memoryChanged) changes.memory_mb = memoryMb

    applyHardware.mutate(changes, {
      onSuccess: (data) => {
        setConfirmOpen(false)
        setDisksToAdd([])
        setNicsToAdd([])
        setNicsToRemove([])
        if (data.nic_failures?.length) {
          setNicFailureWarnings(data.nic_failures)
        }
      },
      onError: (err) => {
        setConfirmOpen(false)
        setApplyError(err.message)
      },
    })
  }

  const primaryNicTarget = hardware.nics[0]?.target ?? ''
  const removingTargets = new Set(nicsToRemove.map((n) => n.target))

  return (
    <div className="space-y-6 max-w-3xl">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate('/vms')}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <h2 className="text-xl font-semibold">Edit Hardware</h2>
      </div>

      {applyError && (
        <div className="text-sm text-red-400 bg-red-400/10 border border-red-400/20 rounded px-3 py-2 flex items-center justify-between">
          <span>{applyError}</span>
          <button onClick={() => setApplyError(null)} className="ml-4 text-red-400 hover:text-red-300">✕</button>
        </div>
      )}

      {nicFailureWarnings.length > 0 && (
        <div className="text-sm text-yellow-300 bg-yellow-500/10 border border-yellow-500/30 rounded px-3 py-2">
          <div className="flex items-center justify-between mb-1">
            <span className="font-medium">Some NIC operations failed — the VM's networking may be inconsistent</span>
            <button onClick={() => setNicFailureWarnings([])} className="ml-4 text-yellow-400 hover:text-yellow-200">✕</button>
          </div>
          <ul className="space-y-0.5 mt-1">
            {nicFailureWarnings.map((f, i) => (
              <li key={i} className="text-yellow-200/80">
                <span className="font-mono">{f.target}</span>: {f.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* CPU & RAM */}
      <Card>
        <CardHeader><CardTitle className="text-sm">CPU &amp; Memory</CardTitle></CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="vcpu">vCPU count</Label>
              <Input
                id="vcpu"
                type="number"
                min={1}
                value={vcpu}
                onChange={(e) => setVcpu(parseInt(e.target.value) || 1)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="memory">RAM (MB)</Label>
              <Input
                id="memory"
                type="number"
                min={512}
                step={512}
                value={memoryMb}
                onChange={(e) => setMemoryMb(parseInt(e.target.value) || 512)}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Disks */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <HardDrive className="h-4 w-4" /> Disks
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Target</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Size</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {hardware.disks.map((disk) => (
                <TableRow key={disk.target}>
                  <TableCell className="font-mono text-sm">{disk.target}</TableCell>
                  <TableCell className="text-[var(--muted-foreground)] text-sm">{disk.device}</TableCell>
                  <TableCell>
                    {disk.device === 'cdrom'
                      ? 'seed ISO'
                      : disk.size_gb > 0
                        ? `${disk.size_gb.toFixed(1)} GB`
                        : '—'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {disksToAdd.length > 0 && (
            <div className="border border-dashed border-[var(--border)] rounded p-3 space-y-1">
              <p className="text-xs text-[var(--muted-foreground)] mb-2">Pending additions</p>
              {disksToAdd.map((d, i) => (
                <div key={i} className="flex items-center justify-between text-sm">
                  <span className="text-green-400">+ new disk — {d.size_gb} GB</span>
                  <button
                    className="text-[var(--muted-foreground)] hover:text-red-400 text-xs"
                    onClick={() => setDisksToAdd((prev) => prev.filter((_, j) => j !== i))}
                  >
                    remove
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="flex items-end gap-3 pt-1">
            <div className="space-y-1 flex-1">
              <Label htmlFor="disk-size" className="text-xs">New disk size (GB)</Label>
              <Input
                id="disk-size"
                type="number"
                min={1}
                placeholder="20"
                value={newDiskSizeGb}
                onChange={(e) => setNewDiskSizeGb(e.target.value)}
              />
            </div>
            <Button size="sm" variant="outline" onClick={handleAddDisk} disabled={!newDiskSizeGb}>
              <Plus className="h-3.5 w-3.5 mr-1" /> Add disk
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* NICs */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Network className="h-4 w-4" /> Network Interfaces
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Target</TableHead>
                <TableHead>Network</TableHead>
                <TableHead>MAC</TableHead>
                <TableHead></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {hardware.nics.map((nic) => {
                const isPrimary = nic.target === primaryNicTarget
                const pendingRemoval = removingTargets.has(nic.target)
                return (
                  <TableRow key={nic.target} className={pendingRemoval ? 'opacity-40 line-through' : ''}>
                    <TableCell className="font-mono text-sm">{nic.target}</TableCell>
                    <TableCell className="text-sm">
                      {nic.network_name
                        ? nic.network_name
                        : nic.vlan_id != null
                          ? `VLAN ${nic.vlan_id} (unknown network)`
                          : <span className="text-[var(--muted-foreground)]">—</span>}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-[var(--muted-foreground)]">{nic.mac}</TableCell>
                    <TableCell>
                      {!pendingRemoval && (
                        <Button
                          variant="ghost"
                          size="icon"
                          title={isPrimary ? 'Primary NIC cannot be removed' : 'Remove NIC'}
                          disabled={isPrimary}
                          onClick={() => handleRemoveNic(nic.target)}
                        >
                          <Trash2 className="h-3.5 w-3.5 text-red-400" />
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>

          {(nicsToAdd.length > 0 || nicsToRemove.length > 0) && (
            <div className="border border-dashed border-[var(--border)] rounded p-3 space-y-1">
              <p className="text-xs text-[var(--muted-foreground)] mb-2">Pending NIC changes</p>
              {nicsToAdd.map((n, i) => (
                <div key={i} className="flex items-center justify-between text-sm">
                  <span className="text-green-400">+ NIC on {n.network_name}</span>
                  <button
                    className="text-[var(--muted-foreground)] hover:text-red-400 text-xs"
                    onClick={() => setNicsToAdd((prev) => prev.filter((_, j) => j !== i))}
                  >
                    cancel
                  </button>
                </div>
              ))}
              {nicsToRemove.map((n, i) => (
                <div key={i} className="flex items-center justify-between text-sm">
                  <span className="text-red-400">− remove {n.target}</span>
                  <button
                    className="text-[var(--muted-foreground)] hover:text-red-400 text-xs"
                    onClick={() => setNicsToRemove((prev) => prev.filter((_, j) => j !== i))}
                  >
                    cancel
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="flex items-end gap-3 pt-1">
            <div className="space-y-1 flex-1">
              <Label className="text-xs">Add NIC on network</Label>
              <Select value={newNicNetworkId} onValueChange={setNewNicNetworkId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select network…" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Select network…</SelectItem>
                  {allNetworks.map((n) => (
                    <SelectItem key={n.id} value={n.id}>
                      {n.name}{n.vlan_id != null ? ` (VLAN ${n.vlan_id})` : ''}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button size="sm" variant="outline" onClick={handleAddNic} disabled={newNicNetworkId === 'none'}>
              <Plus className="h-3.5 w-3.5 mr-1" /> Add NIC
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Pending summary + Apply */}
      {hasChanges && (
        <Card className="border-yellow-500/40 bg-yellow-500/5">
          <CardContent className="py-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-sm font-medium mb-1">Pending changes</p>
                <ul className="text-sm text-[var(--muted-foreground)] space-y-0.5">
                  {pendingSummaryParts.map((p, i) => <li key={i}>{p}</li>)}
                </ul>
              </div>
              <Button onClick={() => setConfirmOpen(true)} disabled={applyHardware.isPending}>
                {applyHardware.isPending ? 'Applying…' : 'Apply changes'}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Confirmation dialog */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-yellow-400" />
              Apply hardware changes?
            </DialogTitle>
          </DialogHeader>

          <div className="space-y-3 py-1">
            {needsReboot && (
              <div className="rounded-md border border-yellow-500/40 bg-yellow-500/10 px-3 py-2 text-sm text-yellow-300">
                CPU, RAM, or disk changes require a <strong>reboot</strong>. The VM will be
                gracefully shut down, reconfigured, and restarted.
              </div>
            )}
            <ul className="text-sm space-y-1 text-[var(--muted-foreground)]">
              {pendingSummaryParts.map((p, i) => <li key={i}>{p}</li>)}
            </ul>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>Cancel</Button>
            <Button onClick={handleApply} disabled={applyHardware.isPending}>
              {applyHardware.isPending ? 'Applying…' : 'Confirm'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
