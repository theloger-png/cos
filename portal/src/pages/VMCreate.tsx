import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useCreateVM } from '@/hooks/useVMs'
import { useTemplates } from '@/hooks/useTemplates'
import { useNodes } from '@/hooks/useNodes'

export function VMCreate() {
  const navigate = useNavigate()
  const createVM = useCreateVM()
  const { data: templates = [] } = useTemplates()
  const { data: nodes = [] } = useNodes()

  const [name, setName] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [nodeId, setNodeId] = useState('auto')
  const [cpuCores, setCpuCores] = useState('')
  const [ramMb, setRamMb] = useState('')
  const [diskGb, setDiskGb] = useState('')

  const selectedTemplate = templates.find((t) => t.id === templateId)
  const onlineNodes = nodes.filter((n) => n.status === 'online')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name || !templateId) return

    createVM.mutate(
      {
        name,
        template_id: templateId,
        node_id: nodeId === 'auto' ? null : nodeId,
        cpu_cores: cpuCores ? parseInt(cpuCores) : undefined,
        ram_mb: ramMb ? parseInt(ramMb) : undefined,
        disk_gb: diskGb ? parseInt(diskGb) : undefined,
      },
      { onSuccess: () => navigate('/vms') },
    )
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate('/vms')}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <h2 className="text-xl font-semibold">Create Virtual Machine</h2>
      </div>

      <Card>
        <CardHeader><CardTitle className="text-sm">VM Configuration</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="space-y-2">
              <Label htmlFor="name">VM Name *</Label>
              <Input
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="my-server"
                required
              />
            </div>

            <div className="space-y-2">
              <Label>Template *</Label>
              <Select value={templateId} onValueChange={setTemplateId} required>
                <SelectTrigger>
                  <SelectValue placeholder="Select a template..." />
                </SelectTrigger>
                <SelectContent>
                  {templates.map((t) => (
                    <SelectItem key={t.id} value={t.id}>
                      {t.name} — {t.cpu_cores} cores, {t.ram_mb >= 1024 ? `${t.ram_mb / 1024}GB` : `${t.ram_mb}MB`} RAM, {t.disk_gb}GB disk
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label>Node (optional — auto if empty)</Label>
              <Select value={nodeId} onValueChange={setNodeId}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">Auto-select</SelectItem>
                  {onlineNodes.map((n) => (
                    <SelectItem key={n.id} value={n.id}>
                      {n.hostname} ({n.ip_address})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="border-t border-[var(--border)] pt-4">
              <p className="text-xs text-[var(--muted-foreground)] mb-4">
                Override template defaults (leave empty to use template values)
              </p>
              <div className="grid grid-cols-3 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="cpu">CPU Cores</Label>
                  <Input
                    id="cpu"
                    type="number"
                    min={1}
                    value={cpuCores}
                    onChange={(e) => setCpuCores(e.target.value)}
                    placeholder={selectedTemplate ? String(selectedTemplate.cpu_cores) : '—'}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="ram">RAM (MB)</Label>
                  <Input
                    id="ram"
                    type="number"
                    min={512}
                    value={ramMb}
                    onChange={(e) => setRamMb(e.target.value)}
                    placeholder={selectedTemplate ? String(selectedTemplate.ram_mb) : '—'}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="disk">Disk (GB)</Label>
                  <Input
                    id="disk"
                    type="number"
                    min={1}
                    value={diskGb}
                    onChange={(e) => setDiskGb(e.target.value)}
                    placeholder={selectedTemplate ? String(selectedTemplate.disk_gb) : '—'}
                  />
                </div>
              </div>
            </div>

            {createVM.error && (
              <p className="text-sm text-red-400">{createVM.error.message}</p>
            )}

            <div className="flex gap-3 pt-2">
              <Button type="submit" disabled={!name || !templateId || createVM.isPending}>
                {createVM.isPending ? 'Creating...' : 'Create VM'}
              </Button>
              <Button type="button" variant="outline" onClick={() => navigate('/vms')}>
                Cancel
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
