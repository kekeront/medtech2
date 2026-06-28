'use client'

import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Search, FileText, X, ExternalLink, Pencil, Check, X as XIcon, Plus, ChevronUp, ChevronDown, ChevronsUpDown, Trash2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api, type Partner, type PriceDocument, type PriceItem } from '@/lib/api'
import { useApi } from '@/lib/use-api'
import { fmtDate, fmtDateTime, fmtKzt } from '@/lib/format'
import { Loading, ErrorBox, Empty } from '@/components/dashboard/state'

const STATUS: Record<string, { label: string; className: string }> = {
  done: { label: 'Обработан', className: 'bg-green-50 text-green-700 border border-green-200' },
  ok: { label: 'Обработан', className: 'bg-green-50 text-green-700 border border-green-200' },
  needs_review: {
    label: 'На проверке',
    className: 'bg-blue-50 text-blue-700 border border-blue-200',
  },
  error: { label: 'Ошибка', className: 'bg-red-50 text-red-700 border border-red-200' },
  dup: { label: 'Дубликат', className: 'bg-gray-50 text-gray-600 border border-gray-200' },
  duplicate: { label: 'Дубликат', className: 'bg-gray-50 text-gray-600 border border-gray-200' },
}

function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, className: 'bg-muted text-muted-foreground' }
  return (
    <span className={cn('rounded-md px-2 py-0.5 text-xs font-medium', s.className)}>
      {s.label}
    </span>
  )
}

// --------------------------------------------------------------------------- preview panel

function FilePreview({ doc }: { doc: PriceDocument }) {
  const fmt = doc.file_format.toLowerCase()
  const previewUrl = api.documentPreviewUrl(doc.doc_id)

  // For PDFs: fetch as a blob and create a blob: URL so Chrome renders inline
  // regardless of the user's "Download PDFs" browser setting.
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [blobError, setBlobError] = useState<string | null>(null)

  useEffect(() => {
    if (fmt !== 'pdf') return
    setBlobUrl(null)
    setBlobError(null)
    let url: string | null = null
    fetch(previewUrl)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.blob()
      })
      .then((blob) => {
        url = URL.createObjectURL(blob)
        setBlobUrl(url)
      })
      .catch((e) => setBlobError(e.message))
    return () => { if (url) URL.revokeObjectURL(url) }
  }, [previewUrl, fmt])

  const iframeCls = 'w-full flex-1 rounded border border-border bg-white'

  if (fmt === 'pdf') {
    if (blobError) return <div className="flex items-center justify-center flex-1 text-sm text-red-500">Ошибка загрузки: {blobError}</div>
    if (!blobUrl) return <Loading />
    return <iframe src={blobUrl} title={doc.file_name} className={iframeCls} style={{ minHeight: 0 }} />
  }

  // xlsx/xls/docx → backend returns HTML, render directly
  return <iframe src={previewUrl} title={doc.file_name} className={iframeCls} style={{ minHeight: 0 }} />
}

type Draft = {
  service_name_raw: string
  section: string
  price_resident_kzt: string
  price_nonresident_kzt: string
  effective_date: string
}

const EMPTY_DRAFT: Draft = { service_name_raw: '', section: '', price_resident_kzt: '', price_nonresident_kzt: '', effective_date: '' }

type SortKey = 'name' | 'code' | 'price' | 'date'

function SortHeader({ label, k, sortKey, sortDir, onClick }: {
  label: string; k: SortKey; sortKey: SortKey; sortDir: 'asc' | 'desc'
  onClick: (k: SortKey) => void
}) {
  const active = sortKey === k
  const Icon = active ? (sortDir === 'asc' ? ChevronUp : ChevronDown) : ChevronsUpDown
  return (
    <button
      onClick={() => onClick(k)}
      className={cn('flex items-center gap-0.5 hover:text-foreground transition-colors', active ? 'text-primary' : 'text-muted-foreground')}
    >
      {label}
      <Icon className="h-3 w-3 shrink-0" />
    </button>
  )
}

function ItemsPreview({ doc }: { doc: PriceDocument }) {
  const [filter, setFilter] = useState('')
  const [sectionFilter, setSectionFilter] = useState('')
  type CellField = 'name' | 'prices' | 'date'
  const [editingCell, setEditingCell] = useState<{ itemId: string; field: CellField } | null>(null)
  const [cellDraft, setCellDraft] = useState({ name: '', resident: '', nonresident: '', date: '' })
  const [cellSaving, setCellSaving] = useState(false)
  const [adding, setAdding] = useState(false)
  const [addDraft, setAddDraft] = useState<Draft>(EMPTY_DRAFT)
  const [addError, setAddError] = useState<string | null>(null)
  const [addSaving, setAddSaving] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>('name')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [editingSection, setEditingSection] = useState<string | null>(null)
  const [sectionDraft, setSectionDraft] = useState('')
  const [sectionSaving, setSectionSaving] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [deleting, setDeleting] = useState(false)

  const { data: rawItems, loading, error, reload } = useApi(
    () => api.documentItems(doc.doc_id),
    [doc.doc_id],
  )
  const [items, setItems] = useState<PriceItem[]>([])
  useEffect(() => { if (rawItems) { setItems(rawItems); setSectionFilter('') } }, [rawItems])

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortKey(key); setSortDir('asc') }
  }

  // name sort: keep section grouping; other sorts: flat global list
  const sorted = useMemo(() => {
    const arr = [...items]
    const dir = sortDir === 'asc' ? 1 : -1
    if (sortKey === 'name') {
      return arr.sort((a, b) => {
        const sa = a.section ?? '￿', sb = b.section ?? '￿'
        if (sa !== sb) return sa.localeCompare(sb, 'ru')
        return dir * a.service_name_raw.localeCompare(b.service_name_raw, 'ru')
      })
    }
    return arr.sort((a, b) => {
      if (sortKey === 'code') {
        const ca = a.service_code_source ?? '￿', cb = b.service_code_source ?? '￿'
        return dir * ca.localeCompare(cb, undefined, { numeric: true })
      }
      if (sortKey === 'price') {
        const pa = a.price_resident_kzt ?? Infinity, pb = b.price_resident_kzt ?? Infinity
        return dir * (pa - pb)
      }
      // date
      const da = a.effective_date ?? '￿', db = b.effective_date ?? '￿'
      return dir * da.localeCompare(db)
    })
  }, [items, sortKey, sortDir])

  const sectionOptions = useMemo(() => {
    const seen = new Set<string>()
    for (const it of items) if (it.section) seen.add(it.section)
    return Array.from(seen).sort((a, b) => a.localeCompare(b, 'ru'))
  }, [items])

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase()
    return sorted.filter((it) => {
      if (sectionFilter && (it.section ?? '') !== sectionFilter) return false
      if (q && !`${it.service_name_raw} ${it.service_code_source ?? ''} ${it.section ?? ''}`.toLowerCase().includes(q)) return false
      return true
    })
  }, [sorted, filter, sectionFilter])

  // Section groups only when sorting by name; flat list otherwise
  const groups = useMemo(() => {
    if (sortKey !== 'name') return [{ section: null as string | null, rows: filtered }]
    const result: { section: string | null; rows: PriceItem[] }[] = []
    for (const it of filtered) {
      const last = result[result.length - 1]
      if (!last || last.section !== it.section) result.push({ section: it.section, rows: [it] })
      else last.rows.push(it)
    }
    return result
  }, [filtered, sortKey])

  const openCell = (it: PriceItem, field: CellField) => {
    setEditingCell({ itemId: it.item_id, field })
    setCellDraft({
      name: it.service_name_raw,
      resident: it.price_resident_kzt != null ? String(it.price_resident_kzt) : '',
      nonresident: it.price_nonresident_kzt != null ? String(it.price_nonresident_kzt) : '',
      date: it.effective_date ?? '',
    })
  }

  const saveCellEdit = async (itemId: string) => {
    if (!editingCell) return
    setCellSaving(true)
    try {
      let patch: Parameters<typeof api.patchItem>[1] = {}
      if (editingCell.field === 'name') patch = { service_name_raw: cellDraft.name || undefined }
      if (editingCell.field === 'prices') patch = {
        price_resident_kzt: cellDraft.resident ? parseFloat(cellDraft.resident) : null,
        price_nonresident_kzt: cellDraft.nonresident ? parseFloat(cellDraft.nonresident) : null,
      }
      if (editingCell.field === 'date') patch = { effective_date: cellDraft.date || null }
      const updated = await api.patchItem(itemId, patch)
      setItems((prev) => prev.map((it) => it.item_id === updated.item_id ? updated : it))
      setEditingCell(null)
    } finally {
      setCellSaving(false)
    }
  }

  const saveNewItem = async () => {
    if (!addDraft.service_name_raw.trim()) return
    setAddSaving(true)
    setAddError(null)
    try {
      const created = await api.createDocumentItem(doc.doc_id, {
        service_name_raw: addDraft.service_name_raw.trim(),
        section: addDraft.section || null,
        price_resident_kzt: addDraft.price_resident_kzt ? parseFloat(addDraft.price_resident_kzt) : null,
        price_nonresident_kzt: addDraft.price_nonresident_kzt ? parseFloat(addDraft.price_nonresident_kzt) : null,
        effective_date: addDraft.effective_date || null,
      })
      setItems((prev) => [created, ...prev])
      setAdding(false)
      setAddDraft(EMPTY_DRAFT)
    } catch (e: unknown) {
      setAddError(e instanceof Error ? e.message : 'Ошибка сохранения')
    } finally {
      setAddSaving(false)
    }
  }

  const saveSectionRename = async (rows: PriceItem[]) => {
    const newSection = sectionDraft.trim() || null
    setSectionSaving(true)
    try {
      const updated = await Promise.all(rows.map((it) => api.patchItem(it.item_id, { section: newSection })))
      const byId = new Map(updated.map((u) => [u.item_id, u]))
      setItems((prev) => prev.map((it) => byId.get(it.item_id) ?? it))
      setEditingSection(null)
    } finally {
      setSectionSaving(false)
    }
  }

  const deleteSelected = async () => {
    if (!selected.size) return
    setDeleting(true)
    try {
      await Promise.all([...selected].map((id) => api.deleteItem(id)))
      setItems((prev) => prev.filter((it) => !selected.has(it.item_id)))
      setSelected(new Set())
    } finally {
      setDeleting(false)
    }
  }

  const toggleRow = (id: string) =>
    setSelected((prev) => { const s = new Set(prev); s.has(id) ? s.delete(id) : s.add(id); return s })

  const allFilteredIds = filtered.map((it) => it.item_id)
  const allChecked = allFilteredIds.length > 0 && allFilteredIds.every((id) => selected.has(id))
  const someChecked = !allChecked && allFilteredIds.some((id) => selected.has(id))

  const toggleAll = () =>
    setSelected(allChecked ? new Set() : new Set(allFilteredIds))

  if (loading) return <Loading />
  if (error) return <ErrorBox message={error} onRetry={reload} />

  const inputCls = 'w-full rounded border border-border bg-background px-2 py-1 text-sm outline-none focus:border-primary'

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="flex items-center gap-2 mx-4 mb-3 shrink-0">
        <div className="flex flex-1 items-center gap-2 rounded-lg border border-border bg-card px-3 py-2">
          <Search className="h-4 w-4 text-muted-foreground shrink-0" />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Фильтр по названию услуги…"
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>
        {sectionOptions.length > 0 && (
          <select
            value={sectionFilter}
            onChange={(e) => setSectionFilter(e.target.value)}
            className="rounded-lg border border-border bg-card px-2 py-2 text-sm text-foreground outline-none focus:border-primary cursor-pointer"
          >
            <option value="">Все разделы</option>
            {sectionOptions.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        )}
      </div>

      <div className="flex-1 overflow-y-auto">
        {groups.length === 0 ? (
          <Empty label="Нет позиций" />
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-card border-b border-border">
              <tr>
                <th className="w-8 pl-3 pr-1 py-2">
                  <input
                    type="checkbox"
                    checked={allChecked}
                    ref={(el) => { if (el) el.indeterminate = someChecked }}
                    onChange={toggleAll}
                    className="h-3.5 w-3.5 cursor-pointer accent-primary"
                  />
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium text-muted-foreground w-[42%]">
                  <div className="flex items-center gap-3">
                    <SortHeader label="Услуга" k="name" sortKey={sortKey} sortDir={sortDir} onClick={toggleSort} />
                    <SortHeader label="Код" k="code" sortKey={sortKey} sortDir={sortDir} onClick={toggleSort} />
                  </div>
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium text-muted-foreground w-[18%]">
                  <SortHeader label="Резидент" k="price" sortKey={sortKey} sortDir={sortDir} onClick={toggleSort} />
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium text-muted-foreground w-[18%]">Нерезидент</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-muted-foreground w-[16%]">
                  <SortHeader label="Дата" k="date" sortKey={sortKey} sortDir={sortDir} onClick={toggleSort} />
                </th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {/* Add-new-item form row */}
              {adding && (
                <>
                  <tr className="border-b border-border bg-green-50">
                    <td className="px-3 py-2 align-top" />
                    <td className="px-4 py-2" colSpan={4}>
                      <input
                        autoFocus
                        className={inputCls + ' font-medium'}
                        value={addDraft.service_name_raw}
                        onChange={(e) => setAddDraft({ ...addDraft, service_name_raw: e.target.value })}
                        placeholder="Название услуги (обязательно)"
                      />
                      <div className="mt-1">
                        <input
                          className={cn(inputCls, 'text-xs')}
                          value={addDraft.section}
                          onChange={(e) => setAddDraft({ ...addDraft, section: e.target.value })}
                          placeholder="Раздел (необязательно)"
                        />
                      </div>
                    </td>
                    <td className="px-2 py-2 align-top" />
                  </tr>
                  <tr className="border-b border-border bg-green-50">
                    <td className="px-3 pb-2" />
                    <td className="px-4 pb-2" />
                    <td className="px-4 pb-2">
                      <input className={inputCls} type="number" value={addDraft.price_resident_kzt} onChange={(e) => setAddDraft({ ...addDraft, price_resident_kzt: e.target.value })} placeholder="Резидент ₸" />
                    </td>
                    <td className="px-4 pb-2">
                      <input className={inputCls} type="number" value={addDraft.price_nonresident_kzt} onChange={(e) => setAddDraft({ ...addDraft, price_nonresident_kzt: e.target.value })} placeholder="Нерезидент ₸" />
                    </td>
                    <td className="px-4 pb-2">
                      <input className={inputCls} type="date" value={addDraft.effective_date} onChange={(e) => setAddDraft({ ...addDraft, effective_date: e.target.value })} />
                    </td>
                    <td className="px-2 pb-2 align-middle">
                      <div className="flex flex-col gap-1">
                        <button onClick={saveNewItem} disabled={addSaving || !addDraft.service_name_raw.trim()} className="rounded p-1 text-green-600 hover:bg-green-100 disabled:opacity-40" title="Сохранить">
                          <Check className="h-4 w-4" />
                        </button>
                        <button onClick={() => { setAdding(false); setAddDraft(EMPTY_DRAFT); setAddError(null) }} disabled={addSaving} className="rounded p-1 text-muted-foreground hover:bg-muted" title="Отмена">
                          <XIcon className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                  {addError && (
                    <tr className="border-b border-border bg-red-50">
                      <td colSpan={5} className="px-4 py-1.5 text-xs text-red-600">{addError}</td>
                    </tr>
                  )}
                </>
              )}
              {groups.map(({ section, rows }) => (
                <React.Fragment key={`g:${section}`}>
                  {/* Section header — hidden in flat-sort modes */}
                  {sortKey === 'name' && (
                    <tr className="bg-muted/60 group/sec">
                      <td colSpan={6} className="px-4 py-1 text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                        {editingSection === (section ?? '') ? (
                          <div className="flex items-center gap-2">
                            <input
                              autoFocus
                              className="rounded border border-border bg-background px-2 py-0.5 text-xs font-semibold outline-none focus:border-primary normal-case tracking-normal w-56"
                              value={sectionDraft}
                              onChange={(e) => setSectionDraft(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') saveSectionRename(rows)
                                if (e.key === 'Escape') setEditingSection(null)
                              }}
                            />
                            <button onClick={() => saveSectionRename(rows)} disabled={sectionSaving} className="rounded p-0.5 text-green-600 hover:bg-green-100 disabled:opacity-40"><Check className="h-3.5 w-3.5" /></button>
                            <button onClick={() => setEditingSection(null)} disabled={sectionSaving} className="rounded p-0.5 text-muted-foreground hover:bg-muted"><XIcon className="h-3.5 w-3.5" /></button>
                            <span className="font-normal normal-case text-muted-foreground/60">({rows.length})</span>
                          </div>
                        ) : (
                          <div className="flex items-center gap-2">
                            <span>{section ?? 'Без раздела'}</span>
                            <span className="font-normal normal-case">({rows.length})</span>
                            <button
                              onClick={() => { setEditingSection(section ?? ''); setSectionDraft(section ?? '') }}
                              className="opacity-0 group-hover/sec:opacity-100 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-opacity"
                              title="Переименовать раздел"
                            >
                              <Pencil className="h-3 w-3" />
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                  {rows.map((it) => {
                    const ec = editingCell?.itemId === it.item_id ? editingCell.field : null
                    const cellBtn = (field: CellField) => (
                      <button
                        onClick={() => openCell(it, field)}
                        className="ml-1 rounded p-0.5 opacity-0 group-hover/row:opacity-100 text-muted-foreground hover:bg-muted hover:text-foreground transition-opacity shrink-0"
                      >
                        <Pencil className="h-3 w-3" />
                      </button>
                    )
                    const saveBtn = (field: CellField) => (
                      <>
                        <button onClick={() => saveCellEdit(it.item_id)} disabled={cellSaving} className="rounded p-0.5 text-green-600 hover:bg-green-100 disabled:opacity-40 shrink-0"><Check className="h-3.5 w-3.5" /></button>
                        <button onClick={() => setEditingCell(null)} disabled={cellSaving} className="rounded p-0.5 text-muted-foreground hover:bg-muted shrink-0"><XIcon className="h-3.5 w-3.5" /></button>
                      </>
                    )
                    return (
                      <tr key={it.item_id} className={cn('border-b border-border last:border-0 hover:bg-muted/30 group/row', selected.has(it.item_id) && 'bg-primary/5')}>
                        <td className="w-8 pl-3 pr-1 py-2">
                          <input
                            type="checkbox"
                            checked={selected.has(it.item_id)}
                            onChange={() => toggleRow(it.item_id)}
                            className="h-3.5 w-3.5 cursor-pointer accent-primary"
                          />
                        </td>
                        {/* Name + code */}
                        <td className="px-4 py-2">
                          {ec === 'name' ? (
                            <div className="flex items-center gap-1">
                              <input
                                autoFocus
                                className={cn(inputCls, 'flex-1')}
                                value={cellDraft.name}
                                onChange={(e) => setCellDraft({ ...cellDraft, name: e.target.value })}
                                onKeyDown={(e) => { if (e.key === 'Enter') saveCellEdit(it.item_id); if (e.key === 'Escape') setEditingCell(null) }}
                              />
                              {saveBtn('name')}
                            </div>
                          ) : (
                            <div className="flex items-center">
                              <span className="text-foreground">{it.service_name_raw}</span>
                              {it.service_code_source && <span className="ml-2 font-mono text-xs text-muted-foreground">{it.service_code_source}</span>}
                              {it.merged_count > 1 && <span className="ml-2 rounded bg-amber-50 px-1 text-xs text-amber-700 border border-amber-200">×{it.merged_count}</span>}
                              {!ec && cellBtn('name')}
                            </div>
                          )}
                        </td>
                        {/* Resident price */}
                        <td className="px-4 py-2 tabular-nums">
                          {ec === 'prices' ? (
                            <input
                              autoFocus
                              className={cn(inputCls, 'w-28')}
                              type="number"
                              value={cellDraft.resident}
                              onChange={(e) => setCellDraft({ ...cellDraft, resident: e.target.value })}
                              onKeyDown={(e) => { if (e.key === 'Escape') setEditingCell(null) }}
                              placeholder="₸"
                            />
                          ) : (
                            <div className="flex items-center">
                              <span className="text-foreground">{fmtKzt(it.price_resident_kzt)}</span>
                              {!ec && cellBtn('prices')}
                            </div>
                          )}
                        </td>
                        {/* Non-resident price */}
                        <td className="px-4 py-2 tabular-nums">
                          {ec === 'prices' ? (
                            <div className="flex items-center gap-1">
                              <input
                                className={cn(inputCls, 'w-28')}
                                type="number"
                                value={cellDraft.nonresident}
                                onChange={(e) => setCellDraft({ ...cellDraft, nonresident: e.target.value })}
                                onKeyDown={(e) => { if (e.key === 'Escape') setEditingCell(null) }}
                                placeholder="₸"
                              />
                              {saveBtn('prices')}
                            </div>
                          ) : (
                            <span className="text-muted-foreground">{fmtKzt(it.price_nonresident_kzt)}</span>
                          )}
                        </td>
                        {/* Date */}
                        <td className="px-4 py-2">
                          {ec === 'date' ? (
                            <div className="flex items-center gap-1">
                              <input
                                autoFocus
                                className={cn(inputCls, 'w-36')}
                                type="date"
                                value={cellDraft.date}
                                onChange={(e) => setCellDraft({ ...cellDraft, date: e.target.value })}
                                onKeyDown={(e) => { if (e.key === 'Enter') saveCellEdit(it.item_id); if (e.key === 'Escape') setEditingCell(null) }}
                              />
                              {saveBtn('date')}
                            </div>
                          ) : (
                            <div className="flex items-center">
                              <span className="text-muted-foreground">{fmtDate(it.effective_date)}</span>
                              {!ec && cellBtn('date')}
                            </div>
                          )}
                        </td>
                        <td className="w-2" />
                      </tr>
                    )
                  })}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="px-4 py-2 border-t border-border flex items-center justify-between shrink-0">
        <span className="text-xs text-muted-foreground">
          Позиций: {filtered.length}{items.length > filtered.length ? ` (из ${items.length})` : ''}
        </span>
        <div className="flex items-center gap-2">
          {selected.size > 0 && (
            <button
              onClick={deleteSelected}
              disabled={deleting}
              className="flex items-center gap-1.5 rounded-lg bg-red-50 border border-red-200 px-3 py-1 text-xs text-red-600 hover:bg-red-100 disabled:opacity-40 transition-colors"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Удалить {selected.size}
            </button>
          )}
          {!adding && (
            <button
              onClick={() => { setAdding(true); setAddDraft(EMPTY_DRAFT); setAddError(null) }}
              className="flex items-center gap-1.5 rounded-lg border border-dashed border-primary px-3 py-1 text-xs text-primary hover:bg-primary/5 transition-colors"
            >
              <Plus className="h-3.5 w-3.5" />
              Добавить позицию
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function DocPanel({
  doc,
  partnerName,
  onClose,
}: {
  doc: PriceDocument
  partnerName: Map<string, string>
  onClose: () => void
}) {
  // Split position as percentage of the panel body height (default 45% for file preview)
  const [splitPct, setSplitPct] = useState(45)
  const panelRef = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)

  const onDividerMouseDown = (e: React.MouseEvent) => {
    e.preventDefault()
    dragging.current = true
    const onMove = (mv: MouseEvent) => {
      if (!dragging.current || !panelRef.current) return
      const rect = panelRef.current.getBoundingClientRect()
      const pct = ((mv.clientY - rect.top) / rect.height) * 100
      setSplitPct(Math.min(80, Math.max(20, pct)))
    }
    const onUp = () => { dragging.current = false; window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return (
    <div className="flex flex-col h-full border-l border-border bg-card">
      {/* Header */}
      <div className="flex items-start gap-3 px-5 py-3 border-b border-border shrink-0">
        <FileText className="h-5 w-5 text-primary mt-0.5 shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-foreground truncate" title={doc.file_name}>
            {doc.file_name}
          </p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {partnerName.get(doc.partner_id) ?? '—'} · {doc.file_format.toUpperCase()} ·{' '}
            {fmtDateTime(doc.parsed_at)}
          </p>
          <div className="mt-1.5 flex items-center gap-2">
            <StatusBadge status={doc.parse_status} />
            <a
              href={api.documentFileUrl(doc.doc_id)}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-xs text-primary hover:underline"
            >
              <ExternalLink className="h-3 w-3" />
              Скачать
            </a>
          </div>
        </div>
        <button
          onClick={onClose}
          className="shrink-0 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Stacked body: file preview + drag handle + items */}
      <div ref={panelRef} className="flex flex-col flex-1 min-h-0 overflow-hidden">
        {/* File preview */}
        <div className="flex flex-col min-h-0 overflow-hidden p-3 pb-0" style={{ height: `${splitPct}%` }}>
          <p className="text-xs font-medium text-muted-foreground mb-2 shrink-0">Предпросмотр файла</p>
          <FilePreview doc={doc} />
        </div>

        {/* Drag handle */}
        <div
          onMouseDown={onDividerMouseDown}
          className="flex items-center justify-center h-3 shrink-0 cursor-row-resize group select-none"
        >
          <div className="w-12 h-1 rounded-full bg-border group-hover:bg-primary transition-colors" />
        </div>

        {/* Items */}
        <div className="flex flex-col min-h-0 overflow-hidden border-t border-border pt-2" style={{ height: `${100 - splitPct}%` }}>
          <p className="text-xs font-medium text-muted-foreground px-4 mb-2 shrink-0">Позиции цен</p>
          <ItemsPreview doc={doc} />
        </div>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- page

export default function DocumentsPage() {
  const [search, setSearch] = useState('')
  const [partnerFilter, setPartnerFilter] = useState('')
  const [selectedDoc, setSelectedDoc] = useState<PriceDocument | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [checkedDocs, setCheckedDocs] = useState<Set<string>>(new Set())
  const [deletingBatch, setDeletingBatch] = useState(false)

  const { data, loading, error, reload } = useApi(
    () => Promise.all([api.listDocuments({ limit: 500 }), api.listPartners()]),
    [],
  )

  const [docs, partnerName] = useMemo(() => {
    const [d, partners] = data ?? [[] as PriceDocument[], [] as Partner[]]
    const names = new Map(partners.map((p) => [p.partner_id, p.name]))
    return [d, names] as const
  }, [data])

  // Partners that actually have documents — drives the filter dropdown (the merged partner view).
  const partnerOptions = useMemo(() => {
    const ids = new Set(docs.map((d) => d.partner_id))
    return Array.from(ids)
      .map((id) => ({ id, name: partnerName.get(id) ?? id.slice(0, 8) }))
      .sort((a, b) => a.name.localeCompare(b.name, 'ru'))
  }, [docs, partnerName])

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    return docs.filter((d) => {
      if (partnerFilter && d.partner_id !== partnerFilter) return false
      if (!q) return true
      return (
        d.file_name.toLowerCase().includes(q) ||
        d.doc_id.toLowerCase().includes(q) ||
        (partnerName.get(d.partner_id) ?? '').toLowerCase().includes(q)
      )
    })
  }, [docs, partnerName, search, partnerFilter])

  const deleteDoc = async (doc: PriceDocument) => {
    if (!window.confirm(`Удалить документ «${doc.file_name}» и все его позиции? Действие необратимо.`)) return
    setDeletingId(doc.doc_id)
    try {
      await api.deleteDocument(doc.doc_id)
      if (selectedDoc?.doc_id === doc.doc_id) setSelectedDoc(null)
      await reload()
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Не удалось удалить документ')
    } finally {
      setDeletingId(null)
    }
  }

  const toggleDocCheck = (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setCheckedDocs((prev) => { const s = new Set(prev); s.has(id) ? s.delete(id) : s.add(id); return s })
  }

  const filteredIds = filtered.map((d) => d.doc_id)
  const allChecked = filteredIds.length > 0 && filteredIds.every((id) => checkedDocs.has(id))
  const someChecked = !allChecked && filteredIds.some((id) => checkedDocs.has(id))
  const toggleAllDocs = (e: React.MouseEvent) => {
    e.stopPropagation()
    setCheckedDocs(allChecked ? new Set() : new Set(filteredIds))
  }

  const deleteCheckedDocs = async () => {
    const ids = [...checkedDocs]
    if (!window.confirm(`Удалить ${ids.length} документ(ов) и все их позиции? Действие необратимо.`)) return
    setDeletingBatch(true)
    try {
      await Promise.all(ids.map((id) => api.deleteDocument(id)))
      if (selectedDoc && checkedDocs.has(selectedDoc.doc_id)) setSelectedDoc(null)
      setCheckedDocs(new Set())
      await reload()
    } finally {
      setDeletingBatch(false)
    }
  }

  const panelOpen = selectedDoc !== null

  return (
    <div className="flex h-full overflow-hidden">
      {/* Main list */}
      <div className={cn('flex flex-col p-6 space-y-6 overflow-auto', panelOpen ? 'w-[38%]' : 'w-full')}>
        <div>
          <h1 className="text-2xl font-bold text-foreground">Документы и партнёры</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Прайс-листы партнёров — фильтруйте по партнёру, открывайте и удаляйте документы
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 w-full max-w-72">
            <Search className="h-4 w-4 text-muted-foreground shrink-0" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск по документам"
              className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>
          <select
            value={partnerFilter}
            onChange={(e) => setPartnerFilter(e.target.value)}
            className="rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground outline-none focus:border-primary cursor-pointer"
          >
            <option value="">Все партнёры ({partnerOptions.length})</option>
            {partnerOptions.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          {partnerFilter && (
            <button
              onClick={() => setPartnerFilter('')}
              className="rounded-lg border border-border px-2.5 py-2 text-xs text-muted-foreground hover:text-foreground hover:bg-muted"
            >
              Сбросить
            </button>
          )}
          {checkedDocs.size > 0 && (
            <button
              onClick={deleteCheckedDocs}
              disabled={deletingBatch}
              className="ml-auto flex items-center gap-1.5 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-600 hover:bg-red-100 disabled:opacity-40 transition-colors"
            >
              <Trash2 className="h-4 w-4" />
              Удалить {checkedDocs.size}
            </button>
          )}
        </div>

        <div className="rounded-xl border border-border bg-card overflow-hidden">
          {loading ? (
            <Loading />
          ) : error ? (
            <ErrorBox message={error} onRetry={reload} />
          ) : filtered.length === 0 ? (
            <Empty label="Документы не найдены" />
          ) : (
            <>
              <table className="w-full">
                <thead>
                  <tr className="border-b border-border">
                    <th className="w-10 pl-4 py-3">
                      <input
                        type="checkbox"
                        checked={allChecked}
                        ref={(el) => { if (el) el.indeterminate = someChecked }}
                        onClick={toggleAllDocs}
                        onChange={() => {}}
                        className="h-3.5 w-3.5 cursor-pointer accent-primary"
                      />
                    </th>
                    {(panelOpen
                      ? ['Файл', 'Статус', '']
                      : ['ID', 'Файл', 'Формат', 'Партнёр', 'Обработан', 'Статус', '']
                    ).map((h, i) => (
                      <th key={i} className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((doc) => {
                    const active = selectedDoc?.doc_id === doc.doc_id
                    const delBtn = (
                      <button
                        onClick={(e) => { e.stopPropagation(); deleteDoc(doc) }}
                        disabled={deletingId === doc.doc_id}
                        title="Удалить документ и все его позиции"
                        className="rounded p-1.5 text-muted-foreground hover:bg-red-50 hover:text-red-600 disabled:opacity-40 transition-colors"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    )
                    return (
                      <tr
                        key={doc.doc_id}
                        onClick={() => setSelectedDoc(active ? null : doc)}
                        className={cn(
                          'border-b border-border last:border-0 cursor-pointer transition-colors',
                          checkedDocs.has(doc.doc_id) ? 'bg-primary/5' : active ? 'bg-primary/5' : 'hover:bg-muted/50',
                        )}
                      >
                        <td className="w-10 pl-4 py-3">
                          <input
                            type="checkbox"
                            checked={checkedDocs.has(doc.doc_id)}
                            onClick={(e) => toggleDocCheck(doc.doc_id, e)}
                            onChange={() => {}}
                            className="h-3.5 w-3.5 cursor-pointer accent-primary"
                          />
                        </td>
                        {panelOpen ? (
                          <>
                            <td className="px-4 py-3">
                              <div className="flex items-center gap-2">
                                <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                                <span className="text-sm text-foreground truncate max-w-[160px]">
                                  {doc.file_name}
                                </span>
                              </div>
                              <p className="text-xs text-muted-foreground mt-0.5 truncate max-w-[160px]">
                                {partnerName.get(doc.partner_id) ?? '—'}
                              </p>
                            </td>
                            <td className="px-4 py-3">
                              <StatusBadge status={doc.parse_status} />
                            </td>
                            <td className="px-2 py-3 text-right">{delBtn}</td>
                          </>
                        ) : (
                          <>
                            <td className="px-4 py-3 text-sm font-mono text-muted-foreground">
                              {doc.doc_id.slice(0, 8)}
                            </td>
                            <td className="px-4 py-3">
                              <div className="flex items-center gap-2">
                                <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                                <span className="text-sm text-foreground">{doc.file_name}</span>
                              </div>
                            </td>
                            <td className="px-4 py-3 text-sm uppercase text-muted-foreground">
                              {doc.file_format}
                            </td>
                            <td className="px-4 py-3 text-sm text-foreground">
                              {partnerName.get(doc.partner_id) ?? '—'}
                            </td>
                            <td className="px-4 py-3 text-sm text-muted-foreground">
                              {fmtDateTime(doc.parsed_at)}
                            </td>
                            <td className="px-4 py-3">
                              <StatusBadge status={doc.parse_status} />
                            </td>
                            <td className="px-2 py-3 text-right">{delBtn}</td>
                          </>
                        )}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              <div className="px-4 py-3 border-t border-border">
                <span className="text-sm text-muted-foreground">Всего: {filtered.length}</span>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Preview panel — stretches to fill height via flex align-items:stretch */}
      {panelOpen && (
        <div className="w-[62%] flex flex-col min-h-0 overflow-hidden">
          <DocPanel
            doc={selectedDoc}
            partnerName={partnerName}
            onClose={() => setSelectedDoc(null)}
          />
        </div>
      )}
    </div>
  )
}
