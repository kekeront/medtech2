'use client'

import { Bell, Settings, HelpCircle, ChevronDown, Search } from 'lucide-react'
import Image from 'next/image'
import { FxRates } from '@/components/dashboard/fx-rates'

export function Header() {
  return (
    <header className="flex h-14 items-center gap-4 border-b border-border bg-background px-6">
      {/* Search */}
      <div className="flex flex-1 items-center gap-2 rounded-lg border border-border bg-muted px-3 py-2 max-w-sm">
        <Search className="h-4 w-4 text-muted-foreground shrink-0" />
        <input
          type="text"
          placeholder="Поиск по системе"
          className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        />
      </div>

      <div className="flex-1" />

      {/* Live NBK exchange rates */}
      <FxRates />

      {/* Divider */}
      <div className="h-6 w-px bg-border" />

      {/* Actions */}
      <div className="flex items-center gap-1">
        <button className="flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted transition-colors">
          <Bell className="h-4.5 w-4.5" />
        </button>
        <button className="flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted transition-colors">
          <Settings className="h-4.5 w-4.5" />
        </button>
        <button className="flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted transition-colors">
          <HelpCircle className="h-4.5 w-4.5" />
        </button>
      </div>

      {/* Divider */}
      <div className="h-6 w-px bg-border" />

      {/* User */}
      <button className="flex items-center gap-2.5">
        <div className="relative h-8 w-8 overflow-hidden rounded-full bg-muted">
          <Image
            src="/placeholder.svg?height=32&width=32"
            alt="Иван Петров"
            width={32}
            height={32}
            className="object-cover"
          />
        </div>
        <div className="text-left leading-none">
          <div className="text-sm font-medium text-foreground">Иван Петров</div>
          <div className="text-xs text-muted-foreground mt-0.5">Аналитик</div>
        </div>
        <ChevronDown className="h-4 w-4 text-muted-foreground" />
      </button>
    </header>
  )
}
