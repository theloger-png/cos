import type { Network, NetworkCreateRequest } from '@/types'
import client from './client'

export async function getNetworks(): Promise<Network[]> {
  const { data } = await client.get<Network[]>('/api/v1/networks')
  return data
}

export async function createNetwork(payload: NetworkCreateRequest): Promise<Network> {
  const { data } = await client.post<Network>('/api/v1/networks', payload)
  return data
}

export async function deleteNetwork(id: string): Promise<void> {
  await client.delete(`/api/v1/networks/${id}`)
}
