import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useTemplates, useCreateTemplate, useDeleteTemplate } from '@/hooks/useTemplates'
import { formatDate } from '@/utils/format'

export function Templates() {
  const { data: templates = [], isLoading } = useTemplates()
  const createTemplate = useCreateTemplate()
  const deleteTemplate = useDeleteTemplate()

  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [cpu, setCpu] = useState('2')
  const [ram, setRam] = useState('2048')
  const [disk, setDisk] = useState('20')
  const [description, setDescription] = useState('')

  const resetForm = () => {
    setName(''); setCpu('2'); setRam('2048'); setDisk('20'); setDescription('')
  }

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault()
    createTemplate.mutate(
      {
        name,
        cpu_cores: parseInt(cpu),
        ram_mb: parseInt(ram),
        disk_gb: parseInt(disk),
        description: description || undefined,
      },
      {
        onSuccess: () => {
          setOpen(false)
          resetForm()
        },
      },
    )
  }

  if (isLoading) return <div className="text-[var(--muted-foreground)]">Loading templates...</div>

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Templates</h2>
        <Button size="sm" onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4 mr-1" /> New Template
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">{templates.length} template{templates.length !== 1 ? 's' : ''}</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>CPU</TableHead>
                <TableHead>RAM</TableHead>
                <TableHead>Disk</TableHead>
                <TableHead>Description</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {templates.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-[var(--muted-foreground)] py-10">
                    No templates yet
                  </TableCell>
                </TableRow>
              ) : (
                templates.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="font-medium">{t.name}</TableCell>
                    <TableCell>{t.cpu_cores} cores</TableCell>
                    <TableCell>{t.ram_mb >= 1024 ? `${t.ram_mb / 1024} GB` : `${t.ram_mb} MB`}</TableCell>
                    <TableCell>{t.disk_gb} GB</TableCell>
                    <TableCell className="text-[var(--muted-foreground)] text-sm">{t.description ?? '—'}</TableCell>
                    <TableCell className="text-[var(--muted-foreground)] text-sm">
                      {formatDate(t.created_at)}
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => deleteTemplate.mutate(t.id)}
                      >
                        <Trash2 className="h-3.5 w-3.5 text-red-400" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) resetForm() }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Template</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleCreate}>
            <div className="space-y-4 py-2">
              <div className="space-y-2">
                <Label>Name *</Label>
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="ubuntu-22.04-small" required />
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div className="space-y-2">
                  <Label>CPU Cores</Label>
                  <Input type="number" min={1} value={cpu} onChange={(e) => setCpu(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>RAM (MB)</Label>
                  <Input type="number" min={512} value={ram} onChange={(e) => setRam(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Disk (GB)</Label>
                  <Input type="number" min={1} value={disk} onChange={(e) => setDisk(e.target.value)} />
                </div>
              </div>
              <div className="space-y-2">
                <Label>Description</Label>
                <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Optional description" />
              </div>
            </div>
            {createTemplate.error && (
              <p className="text-sm text-red-400 mb-3">{createTemplate.error.message}</p>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
              <Button type="submit" disabled={!name || createTemplate.isPending}>
                {createTemplate.isPending ? 'Creating...' : 'Create'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
