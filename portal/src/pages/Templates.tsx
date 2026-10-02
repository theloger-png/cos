import { useState } from 'react'
import { Plus, Trash2, Download, CheckCircle2, XCircle, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Badge } from '@/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { useTemplates, useCreateTemplate, useDeleteTemplate, useFetchTemplateImage } from '@/hooks/useTemplates'
import { formatDate } from '@/utils/format'
import type { FetchImageResponse, Template } from '@/types'

export function Templates() {
  const { data: templates = [], isLoading } = useTemplates()
  const createTemplate = useCreateTemplate()
  const deleteTemplate = useDeleteTemplate()
  const fetchImage = useFetchTemplateImage()

  const [open, setOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<Template | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [osType, setOsType] = useState('')
  const [imageUrl, setImageUrl] = useState('')
  const [cpu, setCpu] = useState('2')
  const [ram, setRam] = useState('2048')
  const [disk, setDisk] = useState('20')
  const [description, setDescription] = useState('')
  const [cloudInitUser, setCloudInitUser] = useState('ubuntu')

  const [downloadTarget, setDownloadTarget] = useState<Template | null>(null)
  const [downloadUrlInput, setDownloadUrlInput] = useState('')
  const [downloadResult, setDownloadResult] = useState<FetchImageResponse | null>(null)

  const closeDelete = () => { setPendingDelete(null); setDeleteError(null) }

  const handleDelete = () => {
    if (!pendingDelete) return
    setDeleteError(null)
    deleteTemplate.mutate(pendingDelete.id, {
      onSuccess: closeDelete,
      onError: (err: Error) => setDeleteError(err.message),
    })
  }

  const resetForm = () => {
    setName(''); setOsType(''); setImageUrl(''); setCpu('2'); setRam('2048'); setDisk('20'); setDescription(''); setCloudInitUser('ubuntu')
  }

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault()
    createTemplate.mutate(
      {
        name,
        os_type: osType,
        image_url: imageUrl,
        cpu_cores: parseInt(cpu),
        ram_mb: parseInt(ram),
        disk_gb: parseInt(disk),
        description: description || undefined,
        cloud_init_user: cloudInitUser || 'ubuntu',
      },
      {
        onSuccess: () => {
          setOpen(false)
          resetForm()
        },
      },
    )
  }

  const openDownload = (t: Template) => {
    fetchImage.reset()
    setDownloadResult(null)
    setDownloadUrlInput(t.image_url ?? '')
    setDownloadTarget(t)
  }

  const closeDownload = () => {
    setDownloadTarget(null)
    setDownloadUrlInput('')
    setDownloadResult(null)
    fetchImage.reset()
  }

  const handleStartDownload = () => {
    if (!downloadTarget) return
    const urlToUse = downloadTarget.image_url ? undefined : downloadUrlInput
    setDownloadResult(null)
    fetchImage.mutate(
      { id: downloadTarget.id, url: urlToUse },
      { onSuccess: (data) => setDownloadResult(data) },
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
                <TableHead>Image</TableHead>
                <TableHead>CPU</TableHead>
                <TableHead>RAM</TableHead>
                <TableHead>Disk</TableHead>
                <TableHead>Cloud-init user</TableHead>
                <TableHead>Description</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {templates.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={9} className="text-center text-[var(--muted-foreground)] py-10">
                    No templates yet
                  </TableCell>
                </TableRow>
              ) : (
                templates.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="font-medium">{t.name}</TableCell>
                    <TableCell>
                      {t.image_url ? (
                        <Badge variant="success" title={t.image_url}>Has image URL</Badge>
                      ) : (
                        <Badge variant="muted" title={t.image_path || undefined}>Local path only</Badge>
                      )}
                    </TableCell>
                    <TableCell>{t.cpu_cores} cores</TableCell>
                    <TableCell>{t.ram_mb >= 1024 ? `${t.ram_mb / 1024} GB` : `${t.ram_mb} MB`}</TableCell>
                    <TableCell>{t.disk_gb} GB</TableCell>
                    <TableCell className="font-mono text-sm">{t.cloud_init_user}</TableCell>
                    <TableCell className="text-[var(--muted-foreground)] text-sm">{t.description ?? '—'}</TableCell>
                    <TableCell className="text-[var(--muted-foreground)] text-sm">
                      {formatDate(t.created_at)}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          title="Download image to all nodes"
                          onClick={() => openDownload(t)}
                        >
                          <Download className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          title="Delete"
                          onClick={() => { setDeleteError(null); setPendingDelete(t) }}
                        >
                          <Trash2 className="h-3.5 w-3.5 text-red-400" />
                        </Button>
                      </div>
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
              <div className="space-y-2">
                <Label>OS Type *</Label>
                <Input value={osType} onChange={(e) => setOsType(e.target.value)} placeholder="ubuntu24.04" required />
              </div>
              <div className="space-y-2">
                <Label>Image URL *</Label>
                <Input
                  value={imageUrl}
                  onChange={(e) => setImageUrl(e.target.value)}
                  placeholder="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
                  required
                />
                <p className="text-xs text-[var(--muted-foreground)]">
                  After creating the template, use the download button to fetch this image to every node.
                </p>
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
                <Label>Cloud-init username</Label>
                <Input
                  value={cloudInitUser}
                  onChange={(e) => setCloudInitUser(e.target.value)}
                  placeholder="ubuntu"
                />
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
              <Button type="submit" disabled={!name || !osType || !imageUrl || createTemplate.isPending}>
                {createTemplate.isPending ? 'Creating...' : 'Create'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={!!pendingDelete}
        title={`Delete template ${pendingDelete?.name}?`}
        description={<p>This cannot be undone.</p>}
        confirmLabel="Delete"
        pendingLabel="Deleting..."
        variant="danger"
        isPending={deleteTemplate.isPending}
        error={deleteError}
        onConfirm={handleDelete}
        onCancel={closeDelete}
      />

      <Dialog open={!!downloadTarget} onOpenChange={(v) => { if (!v) closeDownload() }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Download image for {downloadTarget?.name}</DialogTitle>
          </DialogHeader>

          <div className="space-y-4 py-2">
            {downloadTarget?.image_url ? (
              <div className="space-y-2">
                <Label>Image URL</Label>
                <p className="text-sm font-mono break-all text-[var(--muted-foreground)]">
                  {downloadTarget.image_url}
                </p>
                <p className="text-xs text-[var(--muted-foreground)]">
                  This downloads (or re-syncs) this image to every currently online node.
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                <Label>Image URL *</Label>
                <Input
                  value={downloadUrlInput}
                  onChange={(e) => setDownloadUrlInput(e.target.value)}
                  placeholder="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
                  disabled={fetchImage.isPending}
                />
                <p className="text-xs text-[var(--muted-foreground)]">
                  This template has no image URL yet - give one to download it to every node and
                  switch this template over from a manually-placed local image path.
                </p>
              </div>
            )}

            {fetchImage.isPending && (
              <div className="flex items-center gap-2 text-sm text-[var(--muted-foreground)]">
                <Loader2 className="h-4 w-4 animate-spin" />
                Downloading... this can take several minutes for a large image.
              </div>
            )}

            {fetchImage.error && (
              <p className="text-sm text-red-400">{fetchImage.error.message}</p>
            )}

            {downloadResult && (
              <div className="space-y-2">
                <Label>Results</Label>
                <div className="space-y-1.5">
                  {downloadResult.results.map((r) => (
                    <div key={r.node_id} className="flex items-start gap-2 text-sm">
                      {r.success ? (
                        <CheckCircle2 className="h-4 w-4 text-green-400 mt-0.5 shrink-0" />
                      ) : (
                        <XCircle className="h-4 w-4 text-red-400 mt-0.5 shrink-0" />
                      )}
                      <div>
                        <span className="font-medium">{r.hostname}</span>
                        {!r.success && r.error && (
                          <p className="text-xs text-red-400">{r.error}</p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={closeDownload}>
              {downloadResult ? 'Close' : 'Cancel'}
            </Button>
            {!downloadResult && (
              <Button
                type="button"
                onClick={handleStartDownload}
                disabled={
                  fetchImage.isPending || (!downloadTarget?.image_url && !downloadUrlInput)
                }
              >
                {fetchImage.isPending ? 'Downloading...' : 'Start download'}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
