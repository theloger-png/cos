import type { Operation, OperationFilters } from '@/types'
import client from './client'

export async function getOperations(limit: number, filters: OperationFilters = {}): Promise<Operation[]> {
  const { data } = await client.get<Operation[]>('/api/v1/operations', {
    params: { limit, ...filters },
  })
  return data
}
