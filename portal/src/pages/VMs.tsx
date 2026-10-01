import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Play, Square, Trash2, ArrowRightLeft, Settings2, TerminalSquare, KeyRound } from 'lucide-react'
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
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { CredentialsDialog } from '@/components/CredentialsDialog'
import { useVMs, useStartVM, useStopVM, useDeleteVM, useMigrateVM, useResetVMPassword } from '@/hooks/useVMs'
import { useNodes } from '@/hooks/useNodes'
import { formatDate } from '@/utils/format'
import type { VM } from '@/types'

export function VMs() {
  const navigate = useNavigate()
  const { data: vms = [], isLoading, error } = useVMs()
  const { data: nodes = [] } = useNodes()
  const startVM = useStartVM()
  const stopVM = useStopVM()
  const deleteVM = useDeleteVM()
  const migrateVM = useMigrateVM()
  const resetPassword = useResetVMPassword()

  const [migrateTarget, setMigrateTarget] = useState<VM | null>(null)
  const [targetNodeId, setTargetNodeId] = useState('')
  const [actionError, setActionError] = useState<string | null>(null)
  const [pendingAction, setPendingAction] = useState<{ type: 'stop' | 'delete' | 'reset-password'; vm: VM } | null>(null)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [credentials, setCredentials] = useState<{ vmName: string; user: string; password: string } | null>(null)

  if (isLoading) return <div className="text-[var(--muted-foreground)]">Loading VMs...</div>
  if (error) return <div className="text-red-400">Failed to load VMs: {error.message}</div>

  const onActionError = (err: Error) => setActionError(err.message)

  const closeConfirm = () => { setPendingAction(null); setConfirmError(null) }

  const handleConfirm = () => {
    if (!pendingAction) return
    setConfirmError(null)
    if (pendingAction.type === 'reset-password') {
      const vmName = pendingAction.vm.name
      resetPassword.mutate(pendingAction.vm.id, {
        onSuccess: (data) => {
          setCredentials({ vmName, user: data.user, password: data.password })
          closeConfirm()
        },
        onError: (err: Error) => setConfirmError(err.message),
      })
      return
    }
    const mutation = pendingAction.type === 'stop' ? stopVM : deleteVM
    mutation.mutate(pendingAction.vm.id, {
      onSuccess: closeConfirm,
      onError: (err: Error) => setConfirmError(err.message),
    })
  }

  const confirmPending = stopVM.isPending || deleteVM.isPending || resetPassword.isPending
  const deleteNeedsForce = pendingAction?.type === 'delete' &&
    ['running', 'starting', 'stopping', 'migrating'].includes(pendingAction.vm.status)

  const confirmView = (() => {
    if (!pendingAction) return null
    const { type, vm } = pendingAction
    if (type === 'stop') {
      return {
        title: `Stop ${vm.name}?`,
        description: (
          <p>The VM receives a graceful ACPI shutdown. Applications running inside it will be stopped.</p>
        ),
        confirmLabel: 'Stop',
        pendingLabel: 'Stopping...',
        variant: 'warning' as const,
      }
    }
    if (type === 'reset-password') {
      const isRunning = vm.status === 'running'
      return {
        title: `Reset password for ${vm.name}?`,
        description: (
          <>
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
          </>
        ),
        confirmLabel: 'Reset password',
        pendingLabel: 'Resetting...',
        variant: 'warning' as const,
      }
    }
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
  })()

  const handleMigrate = () => {
    if (!migrateTarget || !targetNodeId) return
    migrateVM.mutate(
      { id: migrateTarget.id, targetNodeId },
      {
        onSuccess: () => { setMigrateTarget(null); setTargetNodeId('') },
        onError: onActionError,
      },
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

      {actionError && (
        <div className="text-sm text-red-400 bg-red-400/10 border border-red-400/20 rounded px-3 py-2 flex items-center justify-between">
          <span>{actionError}</span>
          <button onClick={() => setActionError(null)} className="ml-4 text-red-400 hover:text-red-300">✕</button>
        </div>
      )}

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
                      {formatDate(vm.created_at)}
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        {vm.status === 'stopped' && (
                          <Button
                            variant="ghost"
                            size="icon"
                            title="Start"
                            onClick={() => startVM.mutate(vm.id, { onError: onActionError })}
                          >
                            <Play className="h-3.5 w-3.5 text-green-400" />
                          </Button>
                        )}
                        {vm.status === 'running' && (
                          <Button
                            variant="ghost"
                            size="icon"
                            title="Stop"
                            onClick={() => { setConfirmError(null); setPendingAction({ type: 'stop', vm }) }}
                          >
                            <Square className="h-3.5 w-3.5 text-yellow-400" />
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="icon"
                          title={vm.status === 'running' ? 'Console' : 'Console (VM must be running)'}
                          disabled={vm.status !== 'running'}
                          onClick={() => window.open(`/vms/${vm.id}/console`, '_blank', 'noopener,noreferrer')}
                        >
                          <TerminalSquare className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          title={
                            vm.status === 'running' || vm.status === 'stopped'
                              ? 'Reset password'
                              : 'Reset password (VM must be running or stopped)'
                          }
                          disabled={vm.status !== 'running' && vm.status !== 'stopped'}
                          onClick={() => { setConfirmError(null); setPendingAction({ type: 'reset-password', vm }) }}
                        >
                          <KeyRound className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          title="Edit Hardware"
                          onClick={() => navigate(`/vms/${vm.id}/hardware`)}
                        >
                          <Settings2 className="h-3.5 w-3.5" />
                        </Button>
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
                          onClick={() => { setConfirmError(null); setPendingAction({ type: 'delete', vm }) }}
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
