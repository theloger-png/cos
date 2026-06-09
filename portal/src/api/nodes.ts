import type { Node } from '@/types'
import client from './client'

export async function getNodes(): Promise<Node[]> {
  const { data } = await client.get<Node[]>('/api/v1/nodes')
  return data
}

export async function getNode(id: string): Promise<Node> {
  const { data } = await client.get<Node>(`/api/v1/nodes/${id}`)
  return data
}
