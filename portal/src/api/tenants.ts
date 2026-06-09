import type { Tenant, TenantCreateRequest } from '@/types'
import client from './client'

export async function getTenants(): Promise<Tenant[]> {
  const { data } = await client.get<Tenant[]>('/api/v1/tenants')
  return data
}

export async function createTenant(payload: TenantCreateRequest): Promise<Tenant> {
  const { data } = await client.post<Tenant>('/api/v1/tenants', payload)
  return data
}
