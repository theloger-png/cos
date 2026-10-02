import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft, ArrowRightLeft, ChevronDown, HardDrive, KeyRound, Network, Play,
  Power, RotateCw, Settings2, Square, TerminalSquare, Trash2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { StatusBadge } from '@/components/StatusBadge'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { CredentialsDialog } from '@/components/CredentialsDialog'
import {
  useVM, useVMHardware, useStartVM, useStopVM, useForceStopVM, useRebootVM,
  useDeleteVM, useMigrateVM, useResetVMPassword,
} from '@/hooks/useVMs'
import { useNodes } from '@/hooks/useNodes'
import { useTenants } from '@/hooks/useTenants'
import { useTemplates } from '@/hooks/useTemplates'
import { formatDate } from '@/utils/format'

type PendingType = 'stop' | 'force-stop' | 'reboot' | 'delete' | 'reset-password'

const formatRam = (mb: number) => (mb >= 1024 ? `${mb / 1024} GB` : `${mb} MB`)

export function VMDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { data: vm, isLoading, error } = useVM(id ?? '')
  const { data: hardware, error: hardwareError, isLoading: hardwareLoading } = useVMHardware(id ?? '')
  const { data: nodes = [] } = useNodes()
  const { data: tenants = [] } = useTenants()
  const { data: templates = [] } = useTemplates()
  const startVM = useStartVM()
  const stopVM = useStopVM()
  const forceStopVM = useForceStopVM()
  const rebootVM = useRebootVM()
  const deleteVM = useDeleteVM()
  const migrateVM = useMigrateVM()
  const resetPassword = useResetVMPassword()

  const [pendingAction, setPendingAction] = useState<PendingType | null>(null)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [migrateOpen, setMigrateOpen] = useState(false)
  const [targetNodeId, setTargetNodeId] = useState('')
  const [credentials, setCredentials] = useState<{ vmName: string; user: string; password: string } | null>(null)

  if (isLoading) return <div className="text-[var(--muted-foreground)]">Loading VM...</div>
  if (error || !vm) return <div className="text-red-400">VM not found</div>

  const isRunning = vm.status === 'running'
  const canHardStop = ['running', 'stopping', 'paused'].includes(vm.status)
  const canResetPassword = vm.status === 'running' || vm.status === 'stopped'
  const deleteNeedsForce = ['running', 'starting', 'stopping', 'paused', 'migrating'].includes(vm.status)
  const onActionError = (err: Error) => setActionError(err.message)
  const closeConfirm = () => { setPendingAction(null); setConfirmError(null) }
  const openConfirm = (type: PendingType) => { setConfirmError(null); setPendingAction(type) }

  const handleConfirm = () => {
    if (!pendingAction) return
    setConfirmError(null)
    const onError = (err: Error) => setConfirmError(err.message)
    if (pendingAction === 'reset-password') {
      resetPassword.mutate(vm.id, {
        onSuccess: (data) => {
          setCredentials({ vmName: vm.name, user: data.user, password: data.password })
          closeConfirm()
        },
        onError,
      })
      return
    }
    if (pendingAction === 'delete') {
      deleteVM.mutate(vm.id, { onSuccess: () => navigate('/vms'), onError })
      return
    }
    const mutation = { stop: stopVM, 'force-stop': forceStopVM, reboot: rebootVM }[pendingAction]
    mutation.mutate(vm.id, { onSuccess: closeConfirm, onError })
  }

  const confirmPending =
    stopVM.isPending || forceStopVM.isPending || rebootVM.isPending ||
    deleteVM.isPending || resetPassword.isPending

  const confirmView = (() => {
    switch (pendingAction) {
      case 'stop':
        return {
          title: `Soft stop ${vm.name}?`,
          description: <p>The VM receives a graceful ACPI shutdown. Applications running inside it will be stopped.</p>,
          confirmLabel: 'Soft stop',
          pendingLabel: 'Stopping...',
          variant: 'warning' as const,
        }
      case 'force-stop':
        return {
          title: `Hard stop ${vm.name}?`,
          description: (
            <>
              <p>The VM is killed immediately, without an ACPI shutdown.</p>
              <p className="font-semibold text-red-400">Data not yet written to disk may be lost.</p>
            </>
          ),
          confirmLabel: 'Hard stop',
          pendingLabel: 'Stopping...',
          variant: 'danger' as const,
        }
      case 'reboot':
        return {
          title: `Reboot ${vm.name}?`,
          description: <p>The VM is rebooted. Applications running inside it will be restarted.</p>,
          confirmLabel: 'Reboot',
          pendingLabel: 'Rebooting...',
          variant: 'warning' as const,
        }
      case 'reset-password':
        return {
          title: `Reset password for ${vm.name}?`,
          description: (
            <p>
              A new random password is generated for the VM&apos;s default user.
              {isRunning ? (
                <>
                  {' '}Applied immediately through the guest agent. No reboot is needed.
                  Requires qemu-guest-agent running inside the VM.
                </>
              ) : (
                <>
                  {' '}The VM will be stopped while we edit its password file directly,
                  then powered back on. No guest agent is needed.
                </>
              )}
            </p>
          ),
          confirmLabel: 'Reset password',
          pendingLabel: 'Resetting...',
          variant: 'warning' as const,
        }
      case 'delete':
        return {
          title: `Delete ${vm.name}?`,
          description: (
            <>
              <p>This permanently deletes the VM and its disk. This cannot be undone.</p>
              {deleteNeedsForce && (
                <p className="font-semibold text-red-400">
                  This VM is currently {vm.status} and will be force-stopped.
                </p>
              )}
            </>
          ),
          confirmLabel: 'Delete',
          pendingLabel: 'Deleting...',
          variant: 'danger' as const,
        }
      default:
        return null
    }
  })()

  const handleMigrate = () => {
    if (!targetNodeId) return
    migrateVM.mutate(
      { id: vm.id, targetNodeId },
      {
        onSuccess: () => { setMigrateOpen(false); setTargetNodeId('') },
        onError: onActionError,
      },
    )
  }

  const onlineNodes = nodes.filter((n) => n.status === 'online' && n.id !== vm.node_id)
  const nodeName = nodes.find((n) => n.id === vm.node_id)?.hostname ?? '—'
  const tenantName = tenants.find((t) => t.id === vm.tenant_id)?.name ?? '—'
  const templateName = templates.find((t) => t.id === vm.template_id)?.name ?? '—'
  const allIps = hardware?.nics.flatMap((n) => n.ip_addresses) ?? []

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate('/vms')}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <h2 className="text-xl font-semibold">{vm.name}</h2>
        <StatusBadge status={vm.status} />

        <div className="ml-auto flex items-center gap-2">
          <Button
            size="lg"
            className="bg-blue-600 text-white hover:bg-blue-500"
            disabled={!isRunning}
            title={isRunning ? 'Open serial console in a new tab' : 'Console (VM must be running)'}
            onClick={() => window.open(`/vms/${vm.id}/console`, '_blank', 'noopener,noreferrer')}
          >
            <TerminalSquare className="h-4 w-4 mr-2" /> Console
          </Button>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline">
                Actions <ChevronDown className="h-4 w-4 ml-2" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                disabled={vm.status !== 'stopped'}
                onSelect={() => startVM.mutate(vm.id, { onError: onActionError })}
              >
                <Play className="h-4 w-4 text-green-400" /> Start
              </DropdownMenuItem>
              <DropdownMenuItem disabled={!isRunning} onSelect={() => openConfirm('stop')}>
                <Square className="h-4 w-4 text-yellow-400" /> Soft stop
              </DropdownMenuItem>
              <DropdownMenuItem disabled={!canHardStop} onSelect={() => openConfirm('force-stop')}>
                <Power className="h-4 w-4 text-red-400" /> Hard stop
              </DropdownMenuItem>
              <DropdownMenuItem disabled={!isRunning} onSelect={() => openConfirm('reboot')}>
                <RotateCw className="h-4 w-4" /> Reboot
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => navigate(`/vms/${vm.id}/hardware`)}>
                <Settings2 className="h-4 w-4" /> Edit hardware
              </DropdownMenuItem>
              <DropdownMenuItem disabled={!canResetPassword} onSelect={() => openConfirm('reset-password')}>
                <KeyRound className="h-4 w-4" /> Reset password
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => { setTargetNodeId(''); setMigrateOpen(true) }}>
                <ArrowRightLeft className="h-4 w-4" /> Migrate
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem className="text-red-400" onSelect={() => openConfirm('delete')}>
                <Trash2 className="h-4 w-4" /> Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {actionError && (
        <div className="text-sm text-red-400 bg-red-400/10 border border-red-400/20 rounded px-3 py-2 flex items-center justify-between">
          <span>{actionError}</span>
          <button onClick={() => setActionError(null)} className="ml-4 text-red-400 hover:text-red-300">✕</button>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card>
          <CardHeader><CardTitle className="text-sm">General</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-[var(--muted-foreground)]">ID</span>
              <span className="font-mono text-xs">{vm.id}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--muted-foreground)]">Node</span>
              <span>{nodeName}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--muted-foreground)]">Tenant</span>
              <span>{tenantName}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--muted-foreground)]">Template</span>
              <span>{templateName}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--muted-foreground)]">IP address</span>
              <span className="font-mono">{allIps.length > 0 ? allIps.join(', ') : '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--muted-foreground)]">Created</span>
              <span>{formatDate(vm.created_at)}</span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-sm">Resources</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-[var(--muted-foreground)]">CPU</span>
              <span>{hardware?.vcpu ?? vm.cpu_cores} vCPU</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--muted-foreground)]">RAM</span>
              <span>{formatRam(hardware?.memory_mb ?? vm.ram_mb)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--muted-foreground)]">Disk</span>
              <span>{vm.disk_gb} GB</span>
            </div>
          </CardContent>
        </Card>
      </div>

      {hardwareError ? (
        <div className="text-sm text-[var(--muted-foreground)] border border-[var(--border)] rounded px-3 py-2">
          Disk and network details are unavailable: {hardwareError.message}
        </div>
      ) : hardwareLoading ? (
        <div className="text-sm text-[var(--muted-foreground)]">Loading disk and network details...</div>
      ) : hardware && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm flex items-center gap-2"><HardDrive className="h-4 w-4" /> Disks</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
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
                        {disk.device === 'cdrom' ? 'seed ISO' : disk.size_gb > 0 ? `${disk.size_gb.toFixed(1)} GB` : '—'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm flex items-center gap-2"><Network className="h-4 w-4" /> Network</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>NIC</TableHead>
                    <TableHead>Network</TableHead>
                    <TableHead>MAC</TableHead>
                    <TableHead>IP</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {hardware.nics.map((nic) => (
                    <TableRow key={nic.target}>
                      <TableCell className="font-mono text-sm">{nic.target}</TableCell>
                      <TableCell className="text-sm">
                        {nic.network_name ?? (nic.vlan_id != null ? `VLAN ${nic.vlan_id}` : nic.bridge)}
                      </TableCell>
                      <TableCell className="font-mono text-xs">{nic.mac}</TableCell>
                      <TableCell className="font-mono text-sm">
                        {nic.ip_addresses.length > 0 ? nic.ip_addresses.join(', ') : '—'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Reserved for the upcoming monitoring panel (CPU, RAM, disk, network usage). */}
      <Card>
        <CardHeader><CardTitle className="text-sm">Monitoring</CardTitle></CardHeader>
        <CardContent className="text-sm text-[var(--muted-foreground)] py-10 text-center">
          Resource usage graphs will appear here.
        </CardContent>
      </Card>

      <Dialog open={migrateOpen} onOpenChange={setMigrateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Migrate {vm.name}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <Label>Target Node</Label>
            <Select value={targetNodeId} onValueChange={setTargetNodeId}>
              <SelectTrigger>
                <SelectValue placeholder="Select target node..." />
              </SelectTrigger>
              <SelectContent>
                {onlineNodes.map((n) => (
                  <SelectItem key={n.id} value={n.id}>
                    {n.hostname} ({n.ip_address})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setMigrateOpen(false)}>Cancel</Button>
            <Button onClick={handleMigrate} disabled={!targetNodeId || migrateVM.isPending}>
              {migrateVM.isPending ? 'Migrating...' : 'Migrate'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!confirmView}
        title={confirmView?.title ?? ''}
        description={confirmView?.description}
        confirmLabel={confirmView?.confirmLabel ?? ''}
        pendingLabel={confirmView?.pendingLabel}
        variant={confirmView?.variant}
        isPending={confirmPending}
        error={confirmError}
        onConfirm={handleConfirm}
        onCancel={closeConfirm}
      />

      <CredentialsDialog
        open={credentials !== null}
        user={credentials?.user ?? ''}
        password={credentials?.password ?? ''}
        title={credentials ? `New password for ${credentials.vmName}` : 'New password'}
        dismissLabel="I have saved the password"
        onDismiss={() => setCredentials(null)}
      />
    </div>
  )
}
