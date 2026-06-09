import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createTemplate, deleteTemplate, getTemplates } from '@/api/templates'
import type { TemplateCreateRequest } from '@/types'

export function useTemplates() {
  return useQuery({
    queryKey: ['templates'],
    queryFn: getTemplates,
  })
}

export function useCreateTemplate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: TemplateCreateRequest) => createTemplate(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['templates'] }),
  })
}

export function useDeleteTemplate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteTemplate(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['templates'] }),
  })
}
