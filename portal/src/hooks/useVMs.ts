import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { applyVMHardware, createVM, deleteVM, getVMHardware, getVM, getVMs, migrateVM, rebootVM, resetVMPassword, startVM, stopVM, forceStopVM } from '@/api/vms'
import type { VMCreateRequest, VMCreateResponse, VMHardwareChanges, VMPasswordReset } from '@/types'

export function useVMs() {
  return useQuery({
    queryKey: ['vms'],
    queryFn: getVMs,
    refetchInterval: 30_000,
  })
}

export function useVM(id: string) {
  return useQuery({
    queryKey: ['vms', id],
    queryFn: () => getVM(id),
    enabled: !!id,
    refetchInterval: 30_000,
  })
}

export function useCreateVM() {
  const qc = useQueryClient()
  return useMutation<VMCreateResponse, Error, VMCreateRequest>({
    mutationFn: (payload: VMCreateRequest) => createVM(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['vms'] }),
  })
}

export function useStartVM() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => startVM(id),
    onSettled: () => qc.invalidateQueries({ queryKey: ['operations'] }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['vms'] }),
  })
}

export function useStopVM() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => stopVM(id),
    onSettled: () => qc.invalidateQueries({ queryKey: ['operations'] }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['vms'] }),
  })
}

export function useForceStopVM() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => forceStopVM(id),
    onSettled: () => qc.invalidateQueries({ queryKey: ['operations'] }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['vms'] }),
  })
}

export function useRebootVM() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => rebootVM(id),
    onSettled: () => qc.invalidateQueries({ queryKey: ['operations'] }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['vms'] }),
  })
}

export function useDeleteVM() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteVM(id),
    onSettled: () => qc.invalidateQueries({ queryKey: ['operations'] }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['vms'] }),
  })
}

export function useResetVMPassword() {
  const qc = useQueryClient()
  return useMutation<VMPasswordReset, Error, string>({
    mutationFn: (id: string) => resetVMPassword(id),
    onSettled: () => qc.invalidateQueries({ queryKey: ['operations'] }),
  })
}

export function useMigrateVM() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, targetNodeId }: { id: string; targetNodeId: string }) =>
      migrateVM(id, targetNodeId),
    // Also on failure: the controller may have reconciled the VM's node after a lost reply.
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['vms'] })
      qc.invalidateQueries({ queryKey: ['nodes'] })
      qc.invalidateQueries({ queryKey: ['operations'] })
    },
  })
}

export function useVMHardware(vmId: string) {
  return useQuery({
    queryKey: ['vm-hardware', vmId],
    queryFn: () => getVMHardware(vmId),
    enabled: !!vmId,
  })
}

export function useApplyVMHardware(vmId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (changes: VMHardwareChanges) => applyVMHardware(vmId, changes),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['vm-hardware', vmId] })
      qc.invalidateQueries({ queryKey: ['vms'] })
    },
  })
}
