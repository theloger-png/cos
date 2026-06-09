import { Sun, Moon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useTheme } from '@/hooks/useTheme'

interface TopBarProps {
  title?: string
}

export function TopBar({ title }: TopBarProps) {
  const { theme, toggle } = useTheme()

  return (
    <header className="h-14 flex items-center justify-between px-6 border-b border-[var(--border)] bg-[var(--background)] shrink-0">
      <h1 className="text-base font-semibold text-[var(--foreground)]">{title ?? 'COS Portal'}</h1>
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={toggle} aria-label="Toggle theme">
          {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>
        <div className="h-7 w-7 rounded-full bg-blue-600 flex items-center justify-center text-white text-xs font-medium">
          A
        </div>
      </div>
    </header>
  )
}
