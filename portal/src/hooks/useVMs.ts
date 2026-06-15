import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createVM, deleteVM, getVMs, migrateVM, startVM, stopVM } from '@/api/vms'
import type { VMCreateRequest, VMCreateResponse } from '@/types'

export function useVMs() {
  return useQuery({
    queryKey: ['vms'],
    queryFn: getVMs,
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
    onSuccess: () => qc.invalidateQueries({ queryKey: ['vms'] }),
  })
}

export function useStopVM() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => stopVM(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['vms'] }),
  })
}

export function useDeleteVM() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteVM(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['vms'] }),
  })
}

export function useMigrateVM() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, targetNodeId }: { id: string; targetNodeId: string }) =>
      migrateVM(id, targetNodeId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['vms'] })
      qc.invalidateQueries({ queryKey: ['nodes'] })
    },
  })
}
