import type { FetchImageResponse, Template, TemplateCreateRequest } from '@/types'
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

export async function fetchTemplateImage(id: string, url?: string): Promise<FetchImageResponse> {
  // Can legitimately take several minutes for a large image - no per-request
  // timeout override needed here: axios has no default timeout, and nginx's
  // proxy_read_timeout/proxy_send_timeout are already 3600s (set for the VM
  // console path, which this request shares).
  const { data } = await client.post<FetchImageResponse>(
    `/api/v1/templates/${id}/fetch-image`,
    url ? { url } : {},
  )
  return data
}
