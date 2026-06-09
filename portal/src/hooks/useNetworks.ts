import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createNetwork, deleteNetwork, getNetworks } from '@/api/networks'
import type { NetworkCreateRequest } from '@/types'

export function useNetworks() {
  return useQuery({
    queryKey: ['networks'],
    queryFn: getNetworks,
  })
}

export function useCreateNetwork() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: NetworkCreateRequest) => createNetwork(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['networks'] }),
  })
}

export function useDeleteNetwork() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteNetwork(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['networks'] }),
  })
}
