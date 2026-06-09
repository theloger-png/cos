import { NavLink } from 'react-router-dom'
import { LayoutDashboard, Server, Monitor, Layers, Network, Users } from 'lucide-react'
import { cn } from '@/lib/utils'

const navItems = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/nodes', label: 'Nodes', icon: Server },
  { to: '/vms', label: 'Virtual Machines', icon: Monitor },
  { to: '/templates', label: 'Templates', icon: Layers },
  { to: '/networks', label: 'Networks', icon: Network },
  { to: '/tenants', label: 'Tenants', icon: Users },
]

export function Sidebar() {
  return (
    <aside className="flex flex-col w-60 min-h-screen bg-gray-900 dark:bg-gray-950 border-r border-gray-800 shrink-0">
      <div className="flex items-center gap-2 px-5 py-5 border-b border-gray-800">
        <div className="w-7 h-7 rounded bg-blue-600 flex items-center justify-center text-white font-bold text-sm">
          C
        </div>
        <span className="text-white font-semibold text-base tracking-tight">COS</span>
        <span className="text-gray-500 text-xs">Cloud OS</span>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1">
        {navItems.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors',
                isActive
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-400 hover:text-white hover:bg-gray-800',
              )
            }
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="px-5 py-4 border-t border-gray-800">
        <p className="text-gray-600 text-xs">v0.1.0</p>
      </div>
    </aside>
  )
}
