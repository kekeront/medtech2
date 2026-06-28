'use client'

import { useEffect, useState } from 'react'
import { api, type FxRates } from '@/lib/api'

const fmt = (v: number | undefined) =>
  v == null ? '—' : v.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** Header widget: live National Bank of Kazakhstan rates — ₸ base, USD and RUB per unit. */
export function FxRates() {
  const [data, setData] = useState<FxRates | null>(null)

  useEffect(() => {
    let alive = true
    const load = () =>
      api
        .fxRates()
        .then((d) => alive && setData(d))
        .catch(() => {})
    load()
    const t = setInterval(load, 5 * 60 * 1000) // refresh every 5 minutes
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  if (!data) return null

  const usd = data.rates.USD?.per_unit_kzt
  const rub = data.rates.RUB?.per_unit_kzt
  const title =
    `Курс Нацбанка РК${data.as_of ? ` на ${data.as_of}` : ''}` +
    (data.source !== 'nbk' ? ' · резервные данные (НБ РК недоступен)' : '')

  return (
    <div
      title={title}
      className="hidden md:flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-1.5 text-sm"
    >
      <span className="flex items-center gap-1 font-medium text-foreground">
        <span className="text-base leading-none">🇰🇿</span> ₸
      </span>
      <span className="h-3 w-px bg-border" />
      <Rate flag="🇺🇸" code="USD" value={usd} />
      <span className="h-3 w-px bg-border" />
      <Rate flag="🇷🇺" code="RUB" value={rub} />
      {data.stale && (
        <span
          className="ml-0.5 h-1.5 w-1.5 rounded-full bg-amber-500"
          title="Резервные данные — НБ РК недоступен"
        />
      )}
    </div>
  )
}

function Rate({ flag, code, value }: { flag: string; code: string; value: number | undefined }) {
  return (
    <span className="flex items-center gap-1 tabular-nums text-muted-foreground">
      <span className="text-base leading-none">{flag}</span>
      <span className="text-xs font-medium text-foreground/70">{code}</span>
      <span className="text-foreground">{fmt(value)} ₸</span>
    </span>
  )
}
