'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { Search, ChevronUp, ChevronDown, ChevronsUpDown, TrendingUp } from 'lucide-react'
import { api, type PriceItem } from '@/lib/api'
import { useApi } from '@/lib/use-api'
import { fmtDate, fmtKzt } from '@/lib/format'
import { Loading, ErrorBox, Empty } from '@/components/dashboard/state'
import { PriceDynamicsPanel } from '@/components/dashboard/price-dynamics'

const RENDER_CAP = 1000

type SortKey = 'name' | 'clinic' | 'section' | 'resident' | 'nonresident' | 'date'
const NUMERIC_KEYS: SortKey[] = ['resident', 'nonresident', 'date']

export default function CatalogPage() {
  // Filters
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [clinicId, setClinicId] = useState<string | null>(null) // null=initial, ''=all
  const [year, setYear] = useState('')
  const [section, setSection] = useState('')

  // Sorting
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'name', dir: 'asc' })

  // Price-dynamics panel target
  const [trendItem, setTrendItem] = useState<PriceItem | null>(null)

  // Result rows
  const [items, setItems] = useState<PriceItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reloadTick, setReloadTick] = useState(0)
  const reqId = useRef(0)

  // Partners for the clinic dropdown + name lookup.
  const { data: partners } = useApi(() => api.listPartners(), [])
  const partnerName = useMemo(
    () => new Map((partners ?? []).map((p) => [p.partner_id, p.name])),
    [partners],
  )

  // Default to the first clinic so the page shows data immediately.
  useEffect(() => {
    if (clinicId === null && partners && partners.length) {
      setClinicId(partners[0].partner_id)
    }
  }, [partners, clinicId])

  // Debounce the text query (drives the all-clinics search request).
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 250)
    return () => clearTimeout(t)
  }, [query])

  const allClinics = clinicId === ''
  // Refetch when the clinic changes (per-clinic mode) or when the debounced
  // query changes (all-clinics mode). In per-clinic mode the query filters
  // client-side, so it must NOT be part of the fetch key.
  const fetchKey = `${reloadTick}:${allClinics ? `all:${debounced}` : `clinic:${clinicId ?? ''}`}`

  // Stale year/section filters from a previous result set produce a confusing
  // empty table — clear them whenever the underlying dataset changes.
  useEffect(() => {
    setYear('')
    setSection('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allClinics ? `all:${debounced}` : `clinic:${clinicId ?? ''}`])

  useEffect(() => {
    if (clinicId === null) return // waiting for partners
    const id = ++reqId.current

    if (allClinics) {
      if (debounced.length < 2) {
        setItems([])
        setError(null)
        setLoading(false)
        return
      }
    }
    setLoading(true)
    setError(null)
    const p = allClinics
      ? api.search(debounced).then((r) => r.price_items)
      : api.partnerServices(clinicId as string)
    p.then((rows) => {
      if (id === reqId.current) setItems(rows)
    })
      .catch((e) => {
        if (id === reqId.current) {
          setError(e.message)
          setItems([])
        }
      })
      .finally(() => {
        if (id === reqId.current) setLoading(false)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchKey])

  const clinicNameOf = (it: PriceItem) =>
    it.partner_name ?? partnerName.get(it.partner_id) ?? '—'

  // Year + section options derived from the loaded rows.
  const years = useMemo(() => {
    const set = new Set<string>()
    for (const it of items) {
      const y = it.effective_date?.slice(0, 4)
      if (y) set.add(y)
    }
    return Array.from(set).sort((a, b) => b.localeCompare(a))
  }, [items])

  const sections = useMemo(() => {
    const set = new Set<string>()
    for (const it of items) {
      if (it.section) set.add(it.section)
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b, 'ru'))
  }, [items])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return items.filter((it) => {
      if (year && it.effective_date?.slice(0, 4) !== year) return false
      if (section && it.section !== section) return false
      // In all-clinics mode the server already matched the query (incl. synonyms),
      // so only filter by name client-side in per-clinic mode.
      if (!allClinics && q) {
        const hay = `${it.service_name_raw} ${it.service_code_source ?? ''}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [items, year, section, query, allClinics])

  // Cheapest resident price per service name → highlight best offer (all-clinics view).
  const bestByName = useMemo(() => {
    const m = new Map<string, number>()
    if (!allClinics) return m
    for (const it of filtered) {
      if (it.price_resident_kzt == null) continue
      const k = it.service_name_raw.toLowerCase()
      const cur = m.get(k)
      if (cur == null || it.price_resident_kzt < cur) m.set(k, it.price_resident_kzt)
    }
    return m
  }, [filtered, allClinics])

  // Sort the full filtered set (before the render cap), nulls always last.
  const sorted = useMemo(() => {
    const valueOf = (it: PriceItem): string | number | null => {
      switch (sort.key) {
        case 'name':
          return it.service_name_raw
        case 'clinic':
          return clinicNameOf(it)
        case 'section':
          return it.section || null
        case 'resident':
          return it.price_resident_kzt
        case 'nonresident':
          return it.price_nonresident_kzt
        case 'date':
          return it.effective_date || null
      }
    }
    const arr = [...filtered]
    arr.sort((a, b) => {
      const va = valueOf(a)
      const vb = valueOf(b)
      const ea = va == null || va === ''
      const eb = vb == null || vb === ''
      if (ea && eb) return 0
      if (ea) return 1 // nulls last
      if (eb) return -1
      let c: number
      if (typeof va === 'number' && typeof vb === 'number') c = va - vb
      else c = String(va).localeCompare(String(vb), 'ru')
      return sort.dir === 'asc' ? c : -c
    })
    return arr
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtered, sort, partnerName])

  const toggleSort = (key: SortKey) => {
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: NUMERIC_KEYS.includes(key) ? 'desc' : 'asc' },
    )
  }

  const resetFilters = () => {
    setQuery('')
    setYear('')
    setSection('')
  }
  const hasActiveFilters = Boolean(query || year || section)

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Каталог услуг</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Все услуги и цены из прайсов клиник — фильтры, сортировка по столбцам и динамика цены по годам
        </p>
      </div>

      {/* Toolbar: search + clinic + year + section */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 w-72">
          <Search className="h-4 w-4 text-muted-foreground shrink-0" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={allClinics ? 'Поиск услуги (от 2 символов)…' : 'Поиск по названию услуги…'}
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>

        <select
          value={clinicId ?? ''}
          onChange={(e) => setClinicId(e.target.value)}
          className="rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground outline-none focus:border-primary"
        >
          <option value="">Все клиники</option>
          {(partners ?? []).map((p) => (
            <option key={p.partner_id} value={p.partner_id}>
              {p.name}
            </option>
          ))}
        </select>

        <select
          value={year}
          onChange={(e) => setYear(e.target.value)}
          className="rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground outline-none focus:border-primary"
        >
          <option value="">Все годы</option>
          {years.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>

        <select
          value={section}
          onChange={(e) => setSection(e.target.value)}
          disabled={sections.length === 0}
          className="max-w-56 rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground outline-none focus:border-primary disabled:opacity-50"
        >
          <option value="">Все разделы</option>
          {sections.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        {hasActiveFilters && (
          <button
            onClick={resetFilters}
            className="rounded-lg border border-border px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          >
            Сбросить
          </button>
        )}
      </div>

      {/* Results */}
      <div className="rounded-xl border border-border bg-card overflow-hidden">
        {clinicId === null ? (
          <Loading />
        ) : allClinics && debounced.length < 2 ? (
          <Empty label="Выберите клинику или введите запрос (от 2 символов) для поиска по всем клиникам" />
        ) : loading ? (
          <Loading />
        ) : error ? (
          <ErrorBox message={error} onRetry={() => setReloadTick((t) => t + 1)} />
        ) : sorted.length === 0 ? (
          <Empty label="Ничего не найдено" />
        ) : (
          <>
            <div className="px-5 py-3 border-b border-border text-sm text-muted-foreground">
              Позиций: {sorted.length}
              {sorted.length > RENDER_CAP ? ` (показаны первые ${RENDER_CAP})` : ''}
              {allClinics ? ' · ★ — лучшая цена для резидента по услуге' : ''}
            </div>
            <div className="overflow-x-auto">
            <table className="w-full min-w-[920px]">
              <thead>
                <tr className="border-b border-border">
                  <SortTh label="Услуга" k="name" sort={sort} onSort={toggleSort} />
                  <SortTh label="Клиника" k="clinic" sort={sort} onSort={toggleSort} />
                  <SortTh label="Раздел" k="section" sort={sort} onSort={toggleSort} />
                  <SortTh label="Резидент" k="resident" sort={sort} onSort={toggleSort} align="right" />
                  <SortTh label="Нерезидент" k="nonresident" sort={sort} onSort={toggleSort} align="right" />
                  <SortTh label="Дата" k="date" sort={sort} onSort={toggleSort} align="right" />
                  <th className="w-28 px-3 py-3 text-right text-xs font-medium text-muted-foreground">График</th>
                </tr>
              </thead>
              <tbody>
                {sorted.slice(0, RENDER_CAP).map((it) => {
                  const isBest =
                    allClinics &&
                    it.price_resident_kzt != null &&
                    it.price_resident_kzt === bestByName.get(it.service_name_raw.toLowerCase())
                  return (
                    <tr
                      key={it.item_id}
                      className="border-b border-border last:border-0 hover:bg-muted/50 group/row"
                    >
                      <td className="px-5 py-3 text-sm text-foreground">
                        <button
                          onClick={() => setTrendItem(it)}
                          title="Динамика цены по годам"
                          className="text-left text-foreground hover:text-primary hover:underline transition-colors"
                        >
                          {it.service_name_raw}
                        </button>
                        {it.service_code_source && (
                          <span className="ml-2 font-mono text-xs text-muted-foreground">
                            {it.service_code_source}
                          </span>
                        )}
                        {it.merged_count > 1 && (
                          <span
                            className="ml-2 rounded bg-amber-50 px-1.5 text-xs text-amber-700 border border-amber-200"
                            title={`Объединено ${it.merged_count} одинаковых строк`}
                          >
                            ×{it.merged_count}
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-3 text-sm text-foreground">{clinicNameOf(it)}</td>
                      <td className="px-5 py-3 text-sm text-muted-foreground">{it.section ?? ''}</td>
                      <td
                        className={`px-5 py-3 text-sm tabular-nums text-right ${
                          isBest ? 'font-semibold text-green-600' : 'text-foreground'
                        }`}
                      >
                        {fmtKzt(it.price_resident_kzt)}
                        {isBest && ' ★'}
                      </td>
                      <td className="px-5 py-3 text-sm tabular-nums text-right text-muted-foreground">
                        {fmtKzt(it.price_nonresident_kzt)}
                      </td>
                      <td className="px-5 py-3 text-sm text-right text-muted-foreground">
                        {fmtDate(it.effective_date)}
                      </td>
                      <td className="px-3 py-3 text-right">
                        <button
                          onClick={() => setTrendItem(it)}
                          title="Динамика цены по годам"
                          className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:border-primary hover:bg-primary/5 hover:text-primary transition-colors"
                        >
                          <TrendingUp className="h-3.5 w-3.5" />
                          Динамика
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

      {trendItem && (
        <PriceDynamicsPanel
          itemId={trendItem.item_id}
          serviceName={trendItem.service_name_raw}
          clinicName={clinicNameOf(trendItem)}
          onClose={() => setTrendItem(null)}
        />
      )}
    </div>
  )
}

function SortTh({
  label,
  k,
  sort,
  onSort,
  align = 'left',
}: {
  label: string
  k: SortKey
  sort: { key: SortKey; dir: 'asc' | 'desc' }
  onSort: (k: SortKey) => void
  align?: 'left' | 'right'
}) {
  const active = sort.key === k
  const Icon = !active ? ChevronsUpDown : sort.dir === 'asc' ? ChevronUp : ChevronDown
  return (
    <th className={`px-5 py-3 text-xs font-medium text-muted-foreground ${align === 'right' ? 'text-right' : 'text-left'}`}>
      <button
        onClick={() => onSort(k)}
        className={`inline-flex items-center gap-1 hover:text-foreground transition-colors ${align === 'right' ? 'flex-row-reverse' : ''} ${active ? 'text-foreground' : ''}`}
      >
        {label}
        <Icon className={`h-3.5 w-3.5 ${active ? 'text-primary' : 'text-muted-foreground/60'}`} />
      </button>
    </th>
  )
}
