import { Sun, Moon, LogOut } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { useTheme } from '@/hooks/useTheme'

interface TopBarProps {
  title?: string
}

export function TopBar({ title }: TopBarProps) {
  const { theme, toggle } = useTheme()
  const navigate = useNavigate()
  const username = localStorage.getItem('cos-username') ?? 'admin'
  const initial = username.charAt(0).toUpperCase()

  function handleLogout() {
    localStorage.removeItem('cos-token')
    localStorage.removeItem('cos-username')
    localStorage.removeItem('cos-role')
    navigate('/login')
  }

  return (
    <header className="h-14 flex items-center justify-between px-6 border-b border-[var(--border)] bg-[var(--background)] shrink-0">
      <h1 className="text-base font-semibold text-[var(--foreground)]">{title ?? 'COS Portal'}</h1>
      <div className="flex items-center gap-3">
        <span className="text-sm text-[var(--muted-foreground)]">{username}</span>
        <Button variant="ghost" size="icon" onClick={toggle} aria-label="Toggle theme">
          {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>
        <div className="h-7 w-7 rounded-full bg-blue-600 flex items-center justify-center text-white text-xs font-medium">
          {initial}
        </div>
        <Button variant="ghost" size="icon" onClick={handleLogout} aria-label="Logout">
          <LogOut className="h-4 w-4" />
        </Button>
      </div>
    </header>
  )
}
