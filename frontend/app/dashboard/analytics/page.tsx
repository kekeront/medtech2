'use client'

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
import { api } from '@/lib/api'
import { useApi } from '@/lib/use-api'
import { fmtNumber, fmtPct } from '@/lib/format'
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

export default function AnalyticsPage() {
  const { data: stats, loading, error, reload } = useApi(() => api.stats(), [])

  const statusData = useMemo(() => {
    if (!stats) return []
    return Object.entries(stats.documents_by_status).map(([k, v]) => ({
      key: k,
      label: STATUS_LABEL[k] ?? k,
      value: v,
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
    { label: 'Услуг в справочнике', value: fmtNumber(stats.services) },
    { label: 'Авто-нормализация', value: fmtPct(stats.match_rate_pct) },
    { label: 'На проверке', value: fmtNumber(stats.needs_review) },
  ]

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Отчёты и аналитика</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Сводные показатели по базе услуг и цен
        </p>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-6 gap-4">
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
          {statusData.length === 0 ? (
            <Empty label="Пока нет документов" />
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={statusData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
                <XAxis
                  dataKey="label"
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
                <Bar dataKey="value" name="Документов" radius={[4, 4, 0, 0]} maxBarSize={64}>
                  {statusData.map((b, i) => (
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
                    <span className="font-medium text-foreground">{fmtNumber(item.value)}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
