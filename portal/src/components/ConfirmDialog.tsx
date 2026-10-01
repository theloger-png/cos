import { useRef, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

interface ConfirmDialogProps {
  open: boolean
  title: string
  description: ReactNode
  confirmLabel: string
  pendingLabel?: string
  variant?: 'danger' | 'warning'
  isPending?: boolean
  error?: string | null
  onConfirm: () => void
  onCancel: () => void
}

const CONFIRM_STYLES = {
  danger: 'bg-red-600 text-white hover:bg-red-500',
  warning: 'bg-yellow-500 text-black hover:bg-yellow-400',
} as const

/**
 * Confirmation modal for destructive actions.
 *
 * Cancel receives focus when the dialog opens, so an accidental Enter key
 * never confirms the action. Closing (X, overlay, Esc) is ignored while the
 * action is pending.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  pendingLabel,
  variant = 'danger',
  isPending = false,
  error = null,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null)

  const handleOpenChange = (next: boolean) => {
    if (!next && !isPending) onCancel()
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        onOpenAutoFocus={(e) => {
          e.preventDefault()
          cancelRef.current?.focus()
        }}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription asChild>
            <div className="space-y-2">{description}</div>
          </DialogDescription>
        </DialogHeader>
        {error && (
          <div className="text-sm text-red-400 bg-red-400/10 border border-red-400/20 rounded px-3 py-2">
            {error}
          </div>
        )}
        <DialogFooter className="gap-2 sm:gap-0">
          <Button ref={cancelRef} variant="outline" onClick={onCancel} disabled={isPending}>
            Cancel
          </Button>
          <Button className={CONFIRM_STYLES[variant]} onClick={onConfirm} disabled={isPending}>
            {isPending ? (pendingLabel ?? `${confirmLabel}...`) : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
