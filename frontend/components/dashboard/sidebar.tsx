'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  Home,
  Upload,
  FileText,
  Star,
  BarChart2,
  ShieldCheck,
  ChevronLeft,
  Plus,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useState } from 'react'

const navItems = [
  { href: '/dashboard', label: 'Главная', icon: Home, exact: true },
  { href: '/dashboard/upload', label: 'Загрузка прайс-листов', icon: Upload },
  { href: '/dashboard/documents', label: 'Документы и партнёры', icon: FileText },
  { href: '/dashboard/catalog', label: 'Каталог и поиск услуг', icon: Star },
  { href: '/dashboard/checks', label: 'Проверка', icon: ShieldCheck },
  { href: '/dashboard/analytics', label: 'Отчёты и аналитика', icon: BarChart2 },
]

export function Sidebar() {
  const pathname = usePathname()
  const [collapsed, setCollapsed] = useState(false)

  const isActive = (item: { href: string; exact?: boolean }) => {
    if (item.exact) return pathname === item.href
    return pathname.startsWith(item.href)
  }

  return (
    <aside
      className={cn(
        'flex flex-col border-r border-border bg-sidebar transition-all duration-200 shrink-0',
        collapsed ? 'w-16' : 'w-56',
      )}
    >
      {/* Logo */}
      <div className="flex h-14 items-center gap-2.5 border-b border-border px-4">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary">
          <Plus className="h-4 w-4 text-primary-foreground" strokeWidth={3} />
        </div>
        {!collapsed && (
          <span className="font-semibold text-foreground text-sm">MedPartners</span>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-2">
        {navItems.map((item) => {
          const active = isActive(item)
          const Icon = item.icon
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'flex items-center gap-3 mx-2 px-3 py-2.5 rounded-lg text-sm transition-colors',
                active
                  ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground',
              )}
              title={collapsed ? item.label : undefined}
            >
              <Icon
                className={cn(
                  'shrink-0',
                  collapsed ? 'h-5 w-5' : 'h-4.5 w-4.5',
                  active ? 'text-primary' : '',
                )}
              />
              {!collapsed && <span className="leading-none">{item.label}</span>}
            </Link>
          )
        })}
      </nav>

      {/* Collapse */}
      <div className="border-t border-border p-2">
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
        >
          <ChevronLeft
            className={cn(
              'h-4 w-4 shrink-0 transition-transform',
              collapsed && 'rotate-180',
            )}
          />
          {!collapsed && <span>Свернуть</span>}
        </button>
      </div>
    </aside>
  )
}
