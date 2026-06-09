import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createTenant, getTenants } from '@/api/tenants'
import type { TenantCreateRequest } from '@/types'

export function useTenants() {
  return useQuery({
    queryKey: ['tenants'],
    queryFn: getTenants,
  })
}

export function useCreateTenant() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: TenantCreateRequest) => createTenant(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tenants'] }),
  })
}
