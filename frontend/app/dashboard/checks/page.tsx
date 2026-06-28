'use client'

import { useEffect, useMemo, useState } from 'react'
import { ShieldCheck, Check, AlertTriangle } from 'lucide-react'
import { api, type PriceItem } from '@/lib/api'
import { fmtDate, fmtKzt } from '@/lib/format'
import { Loading, ErrorBox, Empty } from '@/components/dashboard/state'

/** Pull the LLM-added flags out of the combined review_reason string. */
function llmFlags(reason: string | null): string[] {
  if (!reason) return []
  return reason
    .split(';')
    .map((s) => s.trim())
    .filter((s) => s.startsWith('LLM:'))
    .map((s) => s.slice(4).trim())
    .filter(Boolean)
}

export default function ChecksPage() {
  const [items, setItems] = useState<PriceItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)

  // Per-row price drafts + in-flight state.
  const [draft, setDraft] = useState<Record<string, { res: string; non: string }>>({})
  const [saving, setSaving] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    api
      .flagged()
      .then((rows) => {
        if (!alive) return
        setItems(rows)
        setDraft(
          Object.fromEntries(
            rows.map((r) => [
              r.item_id,
              {
                res: r.price_resident_kzt?.toString() ?? '',
                non: r.price_nonresident_kzt?.toString() ?? '',
              },
            ]),
          ),
        )
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : 'Ошибка'))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [reload])

  const partnerName = (it: PriceItem) => it.partner_name ?? '—'

  async function confirm(it: PriceItem) {
    const d = draft[it.item_id]
    const parse = (s: string) => {
      const t = s.trim()
      if (t === '') return undefined
      const n = Number(t.replace(/\s/g, ''))
      return Number.isFinite(n) ? n : undefined
    }
    const res = parse(d?.res ?? '')
    const non = parse(d?.non ?? '')
    // Only send a price when it changed, so an untouched row is just approved as-is.
    const body: { price_resident_kzt?: number; price_nonresident_kzt?: number } = {}
    if (res !== undefined && res !== it.price_resident_kzt) body.price_resident_kzt = res
    if (non !== undefined && non !== it.price_nonresident_kzt) body.price_nonresident_kzt = non
    setSaving(it.item_id)
    try {
      await api.verifyItem(it.item_id, body)
      setItems((prev) => prev.filter((x) => x.item_id !== it.item_id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось подтвердить')
    } finally {
      setSaving(null)
    }
  }

  const total = items.length
  const flaggedCount = useMemo(
    () => items.reduce((n, it) => n + llmFlags(it.review_reason).length, 0),
    [items],
  )

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-foreground">
          <ShieldCheck className="h-6 w-6 text-primary" /> Проверка
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Строки, которые ИИ отметил как подозрительные при разборе прайсов (цены, тарифы,
          мусорные строки). Проверьте, при необходимости поправьте цену и подтвердите.
        </p>
      </div>

      <div className="rounded-xl border border-border bg-card overflow-hidden">
        {loading ? (
          <Loading />
        ) : error ? (
          <ErrorBox message={error} onRetry={() => setReload((t) => t + 1)} />
        ) : total === 0 ? (
          <Empty label="Нет строк на проверку — всё чисто 🎉" />
        ) : (
          <>
            <div className="px-5 py-3 border-b border-border text-sm text-muted-foreground">
              На проверку: {total} {total === 1 ? 'строка' : 'строк'} · {flaggedCount} флаг(ов)
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[920px]">
                <thead>
                  <tr className="border-b border-border">
                    {['Услуга', 'Клиника', 'Резидент', 'Нерезидент', 'Дата', 'Флаги', ''].map(
                      (h) => (
                        <th
                          key={h}
                          className="px-4 py-3 text-left text-xs font-medium text-muted-foreground"
                        >
                          {h}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {items.map((it) => {
                    const d = draft[it.item_id] ?? { res: '', non: '' }
                    const flags = llmFlags(it.review_reason)
                    return (
                      <tr key={it.item_id} className="border-b border-border last:border-0 align-top">
                        <td className="px-4 py-3 text-sm text-foreground max-w-xs">
                          {it.service_name_raw}
                          {it.service_code_source && (
                            <span className="ml-2 font-mono text-xs text-muted-foreground">
                              {it.service_code_source}
                            </span>
                          )}
                          {it.section && (
                            <div className="text-xs text-muted-foreground mt-0.5">{it.section}</div>
                          )}
                        </td>
                        <td className="px-4 py-3 text-sm text-foreground whitespace-nowrap">
                          {partnerName(it)}
                        </td>
                        <td className="px-4 py-3">
                          <input
                            type="number"
                            value={d.res}
                            onChange={(e) =>
                              setDraft((p) => ({ ...p, [it.item_id]: { ...d, res: e.target.value } }))
                            }
                            placeholder="—"
                            className="w-28 rounded-md border border-border bg-card px-2 py-1 text-sm tabular-nums outline-none focus:border-primary"
                          />
                        </td>
                        <td className="px-4 py-3">
                          <input
                            type="number"
                            value={d.non}
                            onChange={(e) =>
                              setDraft((p) => ({ ...p, [it.item_id]: { ...d, non: e.target.value } }))
                            }
                            placeholder="—"
                            className="w-28 rounded-md border border-border bg-card px-2 py-1 text-sm tabular-nums outline-none focus:border-primary"
                          />
                        </td>
                        <td className="px-4 py-3 text-sm text-muted-foreground whitespace-nowrap">
                          {fmtDate(it.effective_date)}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex flex-wrap gap-1 max-w-[16rem]">
                            {flags.length === 0 ? (
                              <span className="text-xs text-muted-foreground">—</span>
                            ) : (
                              flags.map((f, i) => (
                                <span
                                  key={i}
                                  className="inline-flex items-center gap-1 rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-xs text-amber-700"
                                >
                                  <AlertTriangle className="h-3 w-3" />
                                  {f}
                                </span>
                              ))
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right whitespace-nowrap">
                          <button
                            onClick={() => confirm(it)}
                            disabled={saving === it.item_id}
                            className="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                          >
                            <Check className="h-3.5 w-3.5" />
                            {saving === it.item_id ? 'Сохранение…' : 'Подтвердить'}
                          </button>
                        </td>
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
  )
}
