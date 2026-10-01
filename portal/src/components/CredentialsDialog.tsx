import { useState } from 'react'
import { Check, Copy, ShieldAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

interface CredentialsDialogProps {
  open: boolean
  user: string
  password: string
  title?: string
  dismissLabel: string
  onDismiss: () => void
}

type CopyField = 'user' | 'password'

/**
 * One-time credentials modal. The password is never persisted by the
 * backend, so the dialog can only be closed through the explicit dismiss
 * button (Esc and outside clicks are ignored).
 */
export function CredentialsDialog({
  open,
  user,
  password,
  title = 'VM Credentials',
  dismissLabel,
  onDismiss,
}: CredentialsDialogProps) {
  const [copiedField, setCopiedField] = useState<CopyField | null>(null)

  const copyToClipboard = async (field: CopyField, text: string) => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text)
      } else {
        // Legacy fallback for non-secure contexts (plain HTTP)
        const textarea = document.createElement('textarea')
        textarea.value = text
        textarea.style.position = 'fixed'
        textarea.style.opacity = '0'
        // Attach next to the focused element so the dialog focus trap does not steal focus
        const host = document.activeElement?.parentElement ?? document.body
        host.appendChild(textarea)
        textarea.focus()
        textarea.select()
        const ok = document.execCommand('copy')
        host.removeChild(textarea)
        if (!ok) throw new Error('execCommand copy failed')
      }
      setCopiedField(field)
      setTimeout(() => setCopiedField(null), 1500)
    } catch (err) {
      console.error('Failed to copy to clipboard:', err)
    }
  }

  const renderRow = (field: CopyField, label: string, value: string, breakAll = false) => (
    <div className="space-y-1">
      <Label className="text-xs text-[var(--muted-foreground)]">{label}</Label>
      <div className="flex items-center gap-2">
        <code
          className={`flex-1 rounded bg-[var(--muted)] px-3 py-2 text-sm font-mono${breakAll ? ' break-all' : ''}`}
        >
          {value}
        </code>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 shrink-0"
          onClick={() => copyToClipboard(field, value)}
        >
          {copiedField === field ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        </Button>
      </div>
    </div>
  )

  return (
    <Dialog open={open} onOpenChange={() => {}}>
      <DialogContent className="sm:max-w-md" onInteractOutside={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldAlert className="h-5 w-5 text-yellow-400" />
            {title}
          </DialogTitle>
        </DialogHeader>

        <div className="rounded-md border border-yellow-500/40 bg-yellow-500/10 p-3 text-sm text-yellow-300 mb-4">
          Save this password now. It will not be shown again.
        </div>

        <div className="space-y-4">
          {renderRow('user', 'Username', user)}
          {renderRow('password', 'Password', password, true)}
        </div>

        <DialogFooter className="mt-4">
          <Button onClick={onDismiss} className="w-full">
            {dismissLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
