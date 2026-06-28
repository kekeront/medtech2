'use client'

import { useMemo, useState } from 'react'
import { Search } from 'lucide-react'
import Link from 'next/link'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import { useApi } from '@/lib/use-api'
import { Loading, ErrorBox, Empty } from '@/components/dashboard/state'

function StatusBadge({ active }: { active: boolean }) {
  return (
    <span
      className={cn(
        'rounded-md px-2 py-0.5 text-xs font-medium',
        active
          ? 'bg-green-50 text-green-700 border border-green-200'
          : 'bg-gray-50 text-gray-600 border border-gray-200',
      )}
    >
      {active ? 'Активный' : 'Неактивный'}
    </span>
  )
}

export default function PartnersPage() {
  const [search, setSearch] = useState('')
  const { data, loading, error, reload } = useApi(() => api.listPartners(), [])
  const partners = data ?? []

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    if (!q) return partners
    return partners.filter(
      (p) =>
        p.name.toLowerCase().includes(q) || (p.city ?? '').toLowerCase().includes(q),
    )
  }, [partners, search])

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Партнёры</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Клиники-партнёры — откройте карточку, чтобы увидеть прайс и контакты
        </p>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 w-80">
          <Search className="h-4 w-4 text-muted-foreground shrink-0" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Поиск по партнёрам"
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>
      </div>

      {/* Table */}
      <div className="rounded-xl border border-border bg-card overflow-hidden">
        {loading ? (
          <Loading />
        ) : error ? (
          <ErrorBox message={error} onRetry={reload} />
        ) : filtered.length === 0 ? (
          <Empty label="Партнёры не найдены" />
        ) : (
          <>
            <table className="w-full">
              <thead>
                <tr className="border-b border-border">
                  {['ID', 'Название', 'Город', 'Контакты', 'Статус'].map((h) => (
                    <th
                      key={h}
                      className="px-5 py-3 text-left text-xs font-medium text-muted-foreground"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((partner) => {
                  const contact = [partner.contact_phone, partner.contact_email]
                    .filter(Boolean)
                    .join(' · ')
                  return (
                    <tr
                      key={partner.partner_id}
                      className="border-b border-border last:border-0 hover:bg-muted/50"
                    >
                      <td className="px-5 py-3 text-sm text-muted-foreground font-mono">
                        {partner.partner_id.slice(0, 8)}
                      </td>
                      <td className="px-5 py-3">
                        <Link
                          href={`/dashboard/partners/${partner.partner_id}`}
                          className="text-sm font-medium text-foreground hover:text-primary"
                        >
                          {partner.name}
                        </Link>
                      </td>
                      <td className="px-5 py-3 text-sm text-foreground">{partner.city ?? '—'}</td>
                      <td className="px-5 py-3 text-sm text-muted-foreground">
                        {contact || '—'}
                      </td>
                      <td className="px-5 py-3">
                        <StatusBadge active={partner.is_active} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            <div className="flex items-center justify-between px-5 py-3 border-t border-border">
              <span className="text-sm text-muted-foreground">
                Всего: {filtered.length} партнёров
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
