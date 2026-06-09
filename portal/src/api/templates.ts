import type { Template, TemplateCreateRequest } from '@/types'
import client from './client'

export async function getTemplates(): Promise<Template[]> {
  const { data } = await client.get<Template[]>('/api/v1/templates')
  return data
}

export async function getTemplate(id: string): Promise<Template> {
  const { data } = await client.get<Template>(`/api/v1/templates/${id}`)
  return data
}

export async function createTemplate(payload: TemplateCreateRequest): Promise<Template> {
  const { data } = await client.post<Template>('/api/v1/templates', payload)
  return data
}

export async function deleteTemplate(id: string): Promise<void> {
  await client.delete(`/api/v1/templates/${id}`)
}
