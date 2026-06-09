import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getNode, getNodes } from '@/api/nodes'

export function useNodes() {
  return useQuery({
    queryKey: ['nodes'],
    queryFn: getNodes,
    refetchInterval: 30_000,
  })
}

export function useNode(id: string) {
  return useQuery({
    queryKey: ['nodes', id],
    queryFn: () => getNode(id),
    enabled: !!id,
    refetchInterval: 30_000,
  })
}

export function useInvalidateNodes() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: ['nodes'] })
}
