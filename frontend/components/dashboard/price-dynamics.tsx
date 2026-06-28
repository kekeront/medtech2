'use client'

import { useEffect, useState } from 'react'
import { X, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import { api, type ItemPriceHistory } from '@/lib/api'
import { fmtKzt } from '@/lib/format'
import { Loading, ErrorBox, Empty } from '@/components/dashboard/state'

const RESIDENT_COLOR = 'var(--chart-1)' // brand orange
const NONRESIDENT_COLOR = 'var(--chart-3)' // blue

/**
 * Slide-over panel: price dynamics of ONE service across the years.
 * The backend matches the service across the partner's price lists by source
 * code (else normalized name), so the same service is tracked year over year.
 */
export function PriceDynamicsPanel({
  itemId,
  serviceName,
  clinicName,
  onClose,
}: {
  itemId: string
  serviceName: string
  clinicName?: string | null
  onClose: () => void
}) {
  const [data, setData] = useState<ItemPriceHistory | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    api
      .itemHistory(itemId)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e instanceof Error ? e.message : 'Ошибка'))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [itemId])

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const points = data?.points ?? []
  const hasNonresident = points.some((p) => p.price_nonresident_kzt != null)
  const chartData = points.map((p) => ({
    year: String(p.year),
    Резидент: p.price_resident_kzt,
    Нерезидент: p.price_nonresident_kzt,
  }))

  // Overall change across the resident series (first → last priced point).
  const resPoints = points.filter((p) => p.price_resident_kzt != null)
  const firstRes = resPoints[0]?.price_resident_kzt ?? null
  const lastRes = resPoints[resPoints.length - 1]?.price_resident_kzt ?? null
  const overallPct =
    firstRes != null && lastRes != null && firstRes !== 0
      ? ((lastRes - firstRes) / firstRes) * 100
      : null
  const distinctYears = new Set(points.map((p) => p.year)).size

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/40 animate-in fade-in duration-200"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="flex h-full w-full max-w-xl flex-col bg-card shadow-2xl animate-in slide-in-from-right duration-300">
        {/* Header */}
        <div className="flex items-start justify-between gap-4 border-b border-border px-6 py-5">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <TrendingUp className="h-4 w-4 text-primary" /> Динамика цены по годам
            </div>
            <p className="mt-1 truncate text-base font-medium text-foreground">{serviceName}</p>
            <p className="mt-0.5 truncate text-xs text-muted-foreground">
              {clinicName ? `${clinicName} · ` : ''}
              {data?.service_code_source ? (
                <span className="font-mono">{data.service_code_source}</span>
              ) : (
                'без кода'
              )}
              {data ? ` · сопоставление по ${data.matched_by === 'code' ? 'коду' : 'названию'}` : ''}
            </p>
          </div>
          <button
            onClick={onClose}
            className="shrink-0 rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
            aria-label="Закрыть"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <Loading />
          ) : error ? (
            <ErrorBox message={error} />
          ) : points.length === 0 ? (
            <Empty label="Нет дат у этой услуги" />
          ) : (
            <>
              {points.length === 1 && (
                <p className="mb-4 rounded-lg border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
                  Пока одна точка ({points[0].year} г.). График дополнится, когда загрузится прайс
                  этой клиники за другой год.
                </p>
              )}
              {/* Summary stat cards */}
              <div className="mb-5 grid grid-cols-3 gap-3">
                <StatCard label="Текущая цена" value={fmtKzt(lastRes)} />
                <StatCard
                  label="Изменение за период"
                  value={overallPct == null ? '—' : `${overallPct >= 0 ? '+' : ''}${overallPct.toFixed(1)}%`}
                  trend={overallPct}
                />
                <StatCard label="Лет в истории" value={String(distinctYears)} />
              </div>

              {/* Line chart */}
              <div className="h-72 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis dataKey="year" stroke="var(--muted-foreground)" fontSize={12} tickLine={false} />
                    <YAxis
                      stroke="var(--muted-foreground)"
                      fontSize={12}
                      width={64}
                      tickLine={false}
                      tickFormatter={(v) => Number(v).toLocaleString('ru-RU')}
                    />
                    <Tooltip
                      formatter={(v) => fmtKzt(v as number)}
                      labelFormatter={(l) => `${l} год`}
                      contentStyle={{
                        borderRadius: 8,
                        fontSize: 13,
                        border: '1px solid var(--border)',
                        background: 'var(--card)',
                      }}
                    />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="Резидент"
                      stroke={RESIDENT_COLOR}
                      strokeWidth={2.5}
                      dot={{ r: 3, fill: RESIDENT_COLOR }}
                      activeDot={{ r: 5 }}
                      connectNulls
                    />
                    {hasNonresident && (
                      <Line
                        type="monotone"
                        dataKey="Нерезидент"
                        stroke={NONRESIDENT_COLOR}
                        strokeWidth={2.5}
                        dot={{ r: 3, fill: NONRESIDENT_COLOR }}
                        activeDot={{ r: 5 }}
                        connectNulls
                      />
                    )}
                  </LineChart>
                </ResponsiveContainer>
              </div>

              {/* Year-by-year table with deltas */}
              <div className="mt-5 overflow-hidden rounded-xl border border-border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/40 text-xs text-muted-foreground">
                      <th className="px-4 py-2 text-left font-medium">Год</th>
                      <th className="px-4 py-2 text-right font-medium">Резидент</th>
                      <th className="px-4 py-2 text-right font-medium">Нерезидент</th>
                      <th className="px-4 py-2 text-right font-medium">Изменение</th>
                    </tr>
                  </thead>
                  <tbody>
                    {points.map((p, i) => {
                      const prev = i > 0 ? points[i - 1].price_resident_kzt : null
                      const cur = p.price_resident_kzt
                      let chg: string | null = null
                      let chgCls = 'text-muted-foreground'
                      if (prev != null && cur != null && prev !== 0) {
                        const pct = ((cur - prev) / prev) * 100
                        chg = `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`
                        chgCls = pct > 0 ? 'text-red-600' : pct < 0 ? 'text-green-600' : 'text-muted-foreground'
                      }
                      return (
                        <tr key={p.effective_date} className="border-b border-border last:border-0">
                          <td className="px-4 py-2 text-foreground">{p.year}</td>
                          <td className="px-4 py-2 text-right tabular-nums text-foreground">
                            {fmtKzt(p.price_resident_kzt)}
                          </td>
                          <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                            {fmtKzt(p.price_nonresident_kzt)}
                          </td>
                          <td className={`px-4 py-2 text-right tabular-nums ${chgCls}`}>{chg ?? '—'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function StatCard({ label, value, trend }: { label: string; value: string; trend?: number | null }) {
  const TrendIcon = trend == null ? null : trend > 0 ? TrendingUp : trend < 0 ? TrendingDown : Minus
  const trendCls = trend == null ? '' : trend > 0 ? 'text-red-600' : trend < 0 ? 'text-green-600' : 'text-muted-foreground'
  return (
    <div className="rounded-xl border border-border bg-card p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`mt-1 flex items-center gap-1 text-lg font-semibold tabular-nums ${trend != null ? trendCls : 'text-foreground'}`}>
        {TrendIcon && <TrendIcon className="h-4 w-4" />}
        {value}
      </div>
    </div>
  )
}
