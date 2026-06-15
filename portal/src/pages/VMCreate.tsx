import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Copy, ShieldAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { useCreateVM } from '@/hooks/useVMs'
import { useTemplates } from '@/hooks/useTemplates'
import { useNodes } from '@/hooks/useNodes'
import { useTenants } from '@/hooks/useTenants'
import { useNetworks } from '@/hooks/useNetworks'

interface Credentials {
  user: string
  password: string
}

export function VMCreate() {
  const navigate = useNavigate()
  const createVM = useCreateVM()
  const { data: templates = [] } = useTemplates()
  const { data: nodes = [] } = useNodes()
  const { data: tenants = [] } = useTenants()
  const { data: allNetworks = [] } = useNetworks()

  const [name, setName] = useState('')
  const [tenantId, setTenantId] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [nodeId, setNodeId] = useState('auto')
  const [networkId, setNetworkId] = useState('none')
  const [cpuCores, setCpuCores] = useState('')
  const [ramMb, setRamMb] = useState('')
  const [diskGb, setDiskGb] = useState('')

  const [credentials, setCredentials] = useState<Credentials | null>(null)

  const selectedTemplate = templates.find((t) => t.id === templateId)
  const onlineNodes = nodes.filter((n) => n.status === 'online')
  const networks = tenantId
    ? allNetworks.filter((n) => n.tenant_id === tenantId)
    : allNetworks

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name || !templateId || !tenantId) return

    const cpu = cpuCores ? parseInt(cpuCores) : selectedTemplate?.cpu_cores ?? 1
    const ram = ramMb ? parseInt(ramMb) : selectedTemplate?.ram_mb ?? 512
    const disk = diskGb ? parseInt(diskGb) : selectedTemplate?.disk_gb ?? 10

    createVM.mutate(
      {
        name,
        tenant_id: tenantId,
        template_id: templateId,
        node_id: nodeId === 'auto' ? null : nodeId,
        cpu_cores: cpu,
        ram_mb: ram,
        disk_gb: disk,
        network_id: networkId === 'none' ? null : networkId,
      },
      {
        onSuccess: (data) => {
          if (data.cloud_init_password) {
            setCredentials({
              user: data.cloud_init_user ?? 'ubuntu',
              password: data.cloud_init_password,
            })
          } else {
            navigate('/vms')
          }
        },
      },
    )
  }

  const handleCredentialsDismiss = () => {
    setCredentials(null)
    navigate('/vms')
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
              <Label>Tenant *</Label>
              <Select value={tenantId} onValueChange={setTenantId} required>
                <SelectTrigger>
                  <SelectValue placeholder="Select a tenant..." />
                </SelectTrigger>
                <SelectContent>
                  {tenants.map((t) => (
                    <SelectItem key={t.id} value={t.id}>
                      {t.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
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

            <div className="space-y-2">
              <Label>Network (optional)</Label>
              <Select value={networkId} onValueChange={setNetworkId}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">None / default VLAN</SelectItem>
                  {networks.map((n) => (
                    <SelectItem key={n.id} value={n.id}>
                      {n.name}{n.vlan_id != null ? ` (VLAN ${n.vlan_id})` : ''}
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
              <Button type="submit" disabled={!name || !templateId || !tenantId || createVM.isPending}>
                {createVM.isPending ? 'Creating...' : 'Create VM'}
              </Button>
              <Button type="button" variant="outline" onClick={() => navigate('/vms')}>
                Cancel
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <Dialog open={credentials !== null} onOpenChange={() => {}}>
        <DialogContent
          className="sm:max-w-md"
          onInteractOutside={(e) => e.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ShieldAlert className="h-5 w-5 text-yellow-400" />
              VM Credentials
            </DialogTitle>
          </DialogHeader>

          <div className="rounded-md border border-yellow-500/40 bg-yellow-500/10 p-3 text-sm text-yellow-300 mb-4">
            Save this password now. It will not be shown again.
          </div>

          <div className="space-y-4">
            <div className="space-y-1">
              <Label className="text-xs text-[var(--muted-foreground)]">Username</Label>
              <div className="flex items-center gap-2">
                <code className="flex-1 rounded bg-[var(--muted)] px-3 py-2 text-sm font-mono">
                  {credentials?.user}
                </code>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 shrink-0"
                  onClick={() => navigator.clipboard.writeText(credentials?.user ?? '')}
                >
                  <Copy className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>

            <div className="space-y-1">
              <Label className="text-xs text-[var(--muted-foreground)]">Password</Label>
              <div className="flex items-center gap-2">
                <code className="flex-1 rounded bg-[var(--muted)] px-3 py-2 text-sm font-mono break-all">
                  {credentials?.password}
                </code>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 shrink-0"
                  onClick={() => navigator.clipboard.writeText(credentials?.password ?? '')}
                >
                  <Copy className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          </div>

          <DialogFooter className="mt-4">
            <Button onClick={handleCredentialsDismiss} className="w-full">
              I have saved the password — continue
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
