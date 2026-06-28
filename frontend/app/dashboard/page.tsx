'use client'

import Link from 'next/link'
import { useMemo } from 'react'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from 'recharts'
import { Upload, UserPlus, Search, FileText } from 'lucide-react'
import { api } from '@/lib/api'
import { useApi } from '@/lib/use-api'
import { fmtDateTime, fmtNumber, fmtPct } from '@/lib/format'
import { Loading, ErrorBox, Empty } from '@/components/dashboard/state'

const STATUS_LABEL: Record<string, string> = {
  done: 'Обработано',
  ok: 'Обработано',
  needs_review: 'На проверке',
  error: 'Ошибки',
  dup: 'Дубликаты',
  duplicate: 'Дубликаты',
}
const STATUS_COLOR: Record<string, string> = {
  done: '#22c55e',
  ok: '#22c55e',
  needs_review: '#3b82f6',
  error: '#ef4444',
  dup: '#9ca3af',
  duplicate: '#9ca3af',
}

export default function DashboardPage() {
  const { data, loading, error, reload } = useApi(
    () => Promise.all([api.stats(), api.listDocuments({ limit: 8 })]),
    [],
  )
  const [stats, recent] = data ?? [null, []]

  const statusBars = useMemo(() => {
    if (!stats) return []
    return Object.entries(stats.documents_by_status).map(([k, v]) => ({
      status: STATUS_LABEL[k] ?? k,
      count: v,
      color: STATUS_COLOR[k] ?? '#f97316',
    }))
  }, [stats])

  const matchPie = useMemo(() => {
    if (!stats) return []
    const other = Math.max(0, stats.price_items - stats.auto_matched - stats.needs_review)
    return [
      { name: 'Авто-сопоставлено', value: stats.auto_matched, color: '#22c55e' },
      { name: 'На проверке', value: stats.needs_review, color: '#3b82f6' },
      { name: 'Прочее', value: other, color: '#9ca3af' },
    ].filter((d) => d.value > 0)
  }, [stats])

  if (loading)
    return (
      <div className="p-6">
        <Loading />
      </div>
    )
  if (error || !stats)
    return (
      <div className="p-6">
        <ErrorBox message={error ?? 'Нет данных'} onRetry={reload} />
      </div>
    )

  const kpis = [
    { label: 'Документов', value: fmtNumber(stats.documents) },
    { label: 'Позиций цен', value: fmtNumber(stats.price_items) },
    { label: 'Партнёров', value: fmtNumber(stats.partners) },
    { label: 'Авто-нормализация', value: fmtPct(stats.match_rate_pct) },
    { label: 'На проверке', value: fmtNumber(stats.needs_review) },
  ]

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">MedArchive</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Обзор базы услуг и цен клиник-партнёров
          </p>
        </div>
        <Link
          href="/dashboard/upload"
          className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:opacity-90 transition-opacity"
        >
          <Upload className="h-4 w-4" />
          Загрузить прайс-лист
        </Link>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-5 gap-4">
        {kpis.map((kpi) => (
          <div key={kpi.label} className="rounded-xl border border-border bg-card p-4">
            <p className="text-xs text-muted-foreground">{kpi.label}</p>
            <p className="mt-2 text-2xl font-bold text-foreground">{kpi.value}</p>
          </div>
        ))}
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-3 gap-4">
        {/* Documents by status */}
        <div className="col-span-2 rounded-xl border border-border bg-card p-5">
          <h2 className="text-base font-semibold text-foreground mb-4">Документы по статусам</h2>
          {statusBars.length === 0 ? (
            <Empty label="Пока нет обработанных документов" />
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={statusBars} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
                <XAxis
                  dataKey="status"
                  tick={{ fontSize: 12 }}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  allowDecimals={false}
                  tick={{ fontSize: 12 }}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip />
                <Bar dataKey="count" name="Документов" radius={[4, 4, 0, 0]} maxBarSize={64}>
                  {statusBars.map((b, i) => (
                    <Cell key={i} fill={b.color} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Match composition */}
        <div className="rounded-xl border border-border bg-card p-5">
          <h2 className="text-base font-semibold text-foreground mb-4">Сопоставление позиций</h2>
          {matchPie.length === 0 ? (
            <Empty label="Нет позиций" />
          ) : (
            <>
              <div className="flex justify-center">
                <PieChart width={140} height={140}>
                  <Pie
                    data={matchPie}
                    cx={65}
                    cy={65}
                    innerRadius={45}
                    outerRadius={65}
                    paddingAngle={2}
                    dataKey="value"
                    startAngle={90}
                    endAngle={-270}
                  >
                    {matchPie.map((entry, index) => (
                      <Cell key={index} fill={entry.color} />
                    ))}
                  </Pie>
                </PieChart>
              </div>
              <div className="mt-3 space-y-2">
                {matchPie.map((item) => (
                  <div key={item.name} className="flex items-center justify-between text-sm">
                    <div className="flex items-center gap-2">
                      <div
                        className="h-2.5 w-2.5 rounded-full"
                        style={{ backgroundColor: item.color }}
                      />
                      <span className="text-muted-foreground">{item.name}</span>
                    </div>
                    <span className="text-foreground font-medium">{fmtNumber(item.value)}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Bottom row */}
      <div className="grid grid-cols-3 gap-4">
        {/* Recent documents */}
        <div className="col-span-2 rounded-xl border border-border bg-card p-5">
          <h2 className="text-base font-semibold text-foreground mb-4">Последние документы</h2>
          {recent.length === 0 ? (
            <Empty label="Пока нет загруженных документов" />
          ) : (
            <div className="space-y-3">
              {recent.map((doc) => (
                <div key={doc.doc_id} className="flex items-center gap-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 border-primary text-primary">
                    <FileText className="h-4 w-4" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-foreground truncate">{doc.file_name}</p>
                    <p className="text-xs text-muted-foreground">
                      {STATUS_LABEL[doc.parse_status] ?? doc.parse_status}
                    </p>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-xs text-muted-foreground">{fmtDateTime(doc.parsed_at)}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
          <Link
            href="/dashboard/documents"
            className="mt-4 inline-block text-sm text-primary hover:underline"
          >
            Все документы
          </Link>
        </div>

        {/* Quick actions */}
        <div className="rounded-xl border border-border bg-card p-5">
          <h2 className="text-base font-semibold text-foreground mb-4">Быстрые действия</h2>
          <div className="space-y-2">
            {[
              { href: '/dashboard/upload', icon: Upload, label: 'Загрузить прайс-лист' },
              { href: '/dashboard/upload', icon: Upload, label: 'Загрузка прайс-листов' },
              { href: '/dashboard/catalog', icon: Search, label: 'Поиск услуг и цен' },
              { href: '/dashboard/partners', icon: UserPlus, label: 'Партнёры' },
            ].map((action) => {
              const Icon = action.icon
              return (
                <Link
                  key={action.href}
                  href={action.href}
                  className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-foreground hover:bg-muted transition-colors"
                >
                  <Icon className="h-4 w-4 text-muted-foreground shrink-0" />
                  {action.label}
                </Link>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
