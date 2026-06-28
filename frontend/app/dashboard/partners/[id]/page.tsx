'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ChevronLeft, ChevronRight, Search } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import { useApi } from '@/lib/use-api'
import { fmtDate, fmtKzt, fmtNumber } from '@/lib/format'
import { Loading, ErrorBox, Empty } from '@/components/dashboard/state'

const tabs = ['Обзор', 'Услуги'] as const

export default function PartnerDetailPage() {
  const params = useParams<{ id: string }>()
  const id = params.id
  const [activeTab, setActiveTab] = useState(0)
  const [filter, setFilter] = useState('')

  const { data, loading, error, reload } = useApi(
    () => Promise.all([api.getPartner(id), api.partnerServices(id)]),
    [id],
  )

  const [partner, services] = data ?? [null, []]

  const effectiveDates = useMemo(() => {
    const set = new Set<string>()
    for (const s of services) if (s.effective_date) set.add(fmtDate(s.effective_date))
    return Array.from(set)
  }, [services])

  const rawRows = useMemo(
    () => services.reduce((a, s) => a + (s.merged_count || 1), 0),
    [services],
  )

  const filteredServices = useMemo(() => {
    const q = filter.toLowerCase()
    if (!q) return services
    return services.filter(
      (s) =>
        s.service_name_raw.toLowerCase().includes(q) ||
        (s.service_code_source ?? '').toLowerCase().includes(q),
    )
  }, [services, filter])

  if (loading) {
    return (
      <div className="p-6">
        <Loading />
      </div>
    )
  }
  if (error || !partner) {
    return (
      <div className="p-6">
        <ErrorBox message={error ?? 'Партнёр не найден'} onRetry={reload} />
      </div>
    )
  }

  const contacts = [partner.address, partner.contact_phone, partner.contact_email].filter(
    Boolean,
  )

  return (
    <div className="p-6 space-y-6">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm">
        <Link
          href="/dashboard/partners"
          className="flex items-center gap-1 text-muted-foreground hover:text-foreground transition-colors"
        >
          <ChevronLeft className="h-4 w-4" />
          Назад
        </Link>
        <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-muted-foreground">Партнёры</span>
      </div>

      {/* Partner title */}
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold text-foreground">{partner.name}</h1>
        <span
          className={cn(
            'rounded-md px-2.5 py-1 text-sm font-medium border',
            partner.is_active
              ? 'bg-green-50 text-green-700 border-green-200'
              : 'bg-gray-50 text-gray-600 border-gray-200',
          )}
        >
          {partner.is_active ? 'Активный' : 'Неактивный'}
        </span>
        <span className="text-sm text-muted-foreground font-mono">
          ID: {partner.partner_id.slice(0, 8)}
        </span>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-0 border-b border-border">
        {tabs.map((tab, i) => (
          <button
            key={tab}
            onClick={() => setActiveTab(i)}
            className={cn(
              'relative px-4 py-3 text-sm font-medium transition-colors',
              activeTab === i
                ? 'text-primary after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-primary'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {tab}
            {tab === 'Услуги' && (
              <span className="ml-1.5 rounded-full bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                {services.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {activeTab === 0 ? (
        <div className="grid grid-cols-3 gap-5">
          {/* General info */}
          <div className="rounded-xl border border-border bg-card p-5">
            <h2 className="text-base font-semibold text-foreground mb-4">Общая информация</h2>
            <div className="space-y-3">
              <InfoRow label="Название" value={partner.name} />
              <InfoRow label="Город" value={partner.city ?? '—'} />
              <InfoRow label="Адрес" value={partner.address ?? '—'} />
              <InfoRow label="Телефон" value={partner.contact_phone ?? '—'} />
              <InfoRow label="Email" value={partner.contact_email ?? '—'} />
              {contacts.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  Контакты не указаны в прайс-листе
                </p>
              )}
            </div>
          </div>

          {/* Statistics */}
          <div className="rounded-xl border border-border bg-card p-5">
            <h2 className="text-base font-semibold text-foreground mb-4">Статистика</h2>
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div className="rounded-lg border border-border p-4">
                <p className="text-xs text-muted-foreground">Позиций в прайсе</p>
                <p className="mt-2 text-3xl font-bold text-foreground">
                  {fmtNumber(services.length)}
                </p>
              </div>
              <div className="rounded-lg border border-border p-4">
                <p className="text-xs text-muted-foreground">Строк до объединения</p>
                <p className="mt-2 text-3xl font-bold text-foreground">{fmtNumber(rawRows)}</p>
              </div>
            </div>
            <div className="rounded-lg border border-border p-4">
              <p className="text-xs text-muted-foreground">Прайс актуален</p>
              <p className="mt-2 text-2xl font-bold text-foreground">
                {effectiveDates.join(', ') || '—'}
              </p>
            </div>
          </div>

          {/* Contacts */}
          <div className="rounded-xl border border-border bg-card p-5">
            <h2 className="text-base font-semibold text-foreground mb-4">Контакты</h2>
            {contacts.length ? (
              <div className="space-y-3 text-sm text-foreground">
                {contacts.map((c) => (
                  <p key={c as string}>{c}</p>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">Контакты не указаны</p>
            )}
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 w-96">
            <Search className="h-4 w-4 text-muted-foreground shrink-0" />
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Фильтр по услуге внутри прайса…"
              className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>

          <div className="rounded-xl border border-border bg-card overflow-hidden">
            {filteredServices.length === 0 ? (
              <Empty label="Нет позиций" />
            ) : (
              <table className="w-full">
                <thead>
                  <tr className="border-b border-border">
                    {['Раздел', 'Услуга', 'Резидент', 'Нерезидент'].map((h) => (
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
                  {filteredServices.slice(0, 1000).map((s) => (
                    <tr
                      key={s.item_id}
                      className="border-b border-border last:border-0 hover:bg-muted/50"
                    >
                      <td className="px-5 py-3 text-sm text-muted-foreground">
                        {s.section ?? ''}
                      </td>
                      <td className="px-5 py-3 text-sm text-foreground">
                        {s.service_name_raw}
                        {s.service_code_source && (
                          <span className="ml-2 font-mono text-xs text-muted-foreground">
                            {s.service_code_source}
                          </span>
                        )}
                        {s.merged_count > 1 && (
                          <span
                            className="ml-2 rounded bg-amber-50 px-1.5 text-xs text-amber-700 border border-amber-200"
                            title={`Объединено ${s.merged_count} одинаковых строк`}
                          >
                            ×{s.merged_count}
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-3 text-sm tabular-nums text-foreground">
                        {fmtKzt(s.price_resident_kzt)}
                      </td>
                      <td className="px-5 py-3 text-sm tabular-nums text-muted-foreground">
                        {fmtKzt(s.price_nonresident_kzt)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {filteredServices.length > 1000 && (
              <div className="px-5 py-3 border-t border-border text-sm text-muted-foreground">
                Показаны первые 1000 из {filteredServices.length} — уточните фильтром.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start gap-4">
      <span className="w-20 shrink-0 text-sm text-muted-foreground">{label}</span>
      <span className="text-sm text-foreground">{value}</span>
    </div>
  )
}
