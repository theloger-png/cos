import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { getOperations } from '@/api/operations'
import type { OperationFilters } from '@/types'

export const OPERATIONS_POLL_MS = 2_000

/** Poll the operation log every 2s (newest first). */
export function useOperations(limit: number, filters: OperationFilters = {}) {
  return useQuery({
    queryKey: ['operations', limit, filters],
    queryFn: () => getOperations(limit, filters),
    refetchInterval: OPERATIONS_POLL_MS,
    // Keep the old rows on screen while a filter/limit change loads, so the
    // console does not flash empty.
    placeholderData: keepPreviousData,
    staleTime: 0,
  })
}
