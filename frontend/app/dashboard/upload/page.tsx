'use client'

import { useEffect, useRef, useState } from 'react'
import { Upload, FileText, CloudUpload, Loader2, CheckCircle2, AlertCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api, type JobStatus, type PriceDocument, type Partner } from '@/lib/api'
import { fmtDateTime, fmtNumber } from '@/lib/format'
import { Loading, ErrorBox, Empty } from '@/components/dashboard/state'

const ACCEPT = '.pdf,.docx,.doc,.xlsx,.xls,.zip'

export default function UploadPage() {
  const [isDragging, setIsDragging] = useState(false)
  const [fileName, setFileName] = useState<string | null>(null)
  const [job, setJob] = useState<JobStatus | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // History from the backend; refetched after each successful ingest.
  const [history, setHistory] = useState<PriceDocument[]>([])
  const [partners, setPartners] = useState<Map<string, string>>(new Map())
  const [historyState, setHistoryState] = useState<'loading' | 'ready' | 'error'>('loading')

  const loadHistory = async () => {
    try {
      const [docs, ps] = await Promise.all([
        api.listDocuments({ limit: 50 }),
        api.listPartners(),
      ])
      setHistory(docs)
      setPartners(new Map(ps.map((p: Partner) => [p.partner_id, p.name])))
      setHistoryState('ready')
    } catch {
      setHistoryState('error')
    }
  }

  useEffect(() => {
    loadHistory()
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current)
    }
  }, [])

  const poll = (jobId: string) => {
    const tick = async () => {
      try {
        const j = await api.job(jobId)
        setJob(j)
        if (j.status === 'done' || j.status === 'error') {
          setBusy(false)
          loadHistory()
          return
        }
      } catch (e) {
        setUploadError(e instanceof Error ? e.message : 'Ошибка опроса задачи')
        setBusy(false)
        return
      }
      pollRef.current = setTimeout(tick, 1000)
    }
    tick()
  }

  const handleFile = async (file: File) => {
    setUploadError(null)
    setJob(null)
    setFileName(file.name)
    setBusy(true)
    try {
      const ack = await api.upload(file)
      poll(ack.job_id)
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Ошибка загрузки')
      setBusy(false)
    }
  }

  const report = job?.reports?.[0]

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Загрузка прайс-листов</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Загрузите прайс-лист партнёра (или .zip с несколькими) для автоматической обработки
        </p>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Drop zone */}
        <div
          className={cn(
            'col-span-2 rounded-xl border-2 border-dashed border-border bg-card flex flex-col items-center justify-center gap-4 py-16 cursor-pointer transition-colors',
            isDragging && 'border-primary bg-brand-light',
            busy && 'pointer-events-none opacity-60',
          )}
          onDragOver={(e) => {
            e.preventDefault()
            setIsDragging(true)
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setIsDragging(false)
            if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0])
          }}
          onClick={() => inputRef.current?.click()}
        >
          <input
            ref={inputRef}
            type="file"
            className="hidden"
            accept={ACCEPT}
            onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
          />
          <CloudUpload className="h-14 w-14 text-muted-foreground" strokeWidth={1} />
          <p className="text-base text-muted-foreground">Перетащите файл сюда или</p>
          <button
            onClick={(e) => {
              e.stopPropagation()
              inputRef.current?.click()
            }}
            className="flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground hover:opacity-90 transition-opacity"
          >
            <Upload className="h-4 w-4" />
            Выбрать файл
          </button>
          <div className="text-center text-xs text-muted-foreground space-y-1">
            <p>Поддерживаемые форматы: PDF, DOCX, XLSX, XLS, ZIP (включая сканы)</p>
          </div>
        </div>

        {/* Current upload status */}
        <div className="rounded-xl border border-border bg-card p-5">
          <h2 className="text-sm font-semibold text-foreground mb-4">Текущая загрузка</h2>

          {!fileName && !uploadError ? (
            <p className="text-sm text-muted-foreground">Файл ещё не выбран</p>
          ) : (
            <>
              <div className="flex items-center gap-2 mb-4">
                <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
                <span className="text-xs text-foreground flex-1 truncate">{fileName}</span>
                {busy ? (
                  <Loader2 className="h-4 w-4 animate-spin text-primary" />
                ) : uploadError || job?.status === 'error' ? (
                  <AlertCircle className="h-4 w-4 text-destructive" />
                ) : (
                  <CheckCircle2 className="h-4 w-4 text-green-500" />
                )}
              </div>

              {uploadError ? (
                <p className="text-sm text-destructive">{uploadError}</p>
              ) : job ? (
                <div className="space-y-2 text-sm">
                  <StatusLine label="Статус" value={statusLabel(job.status)} />
                  {job.total > 1 && (
                    <StatusLine label="Файлов" value={`${job.done} / ${job.total}`} />
                  )}
                  {job.current && <StatusLine label="Файл" value={job.current} />}
                  {report && (
                    <>
                      {report.partner && (
                        <StatusLine label="Партнёр" value={report.partner} />
                      )}
                      <StatusLine label="Извлечено строк" value={fmtNumber(report.rows_parsed)} />
                      <StatusLine label="Записано" value={fmtNumber(report.rows_written)} />
                      <StatusLine
                        label="На проверку"
                        value={fmtNumber(report.rows_needs_review)}
                      />
                      <StatusLine label="Пропущено" value={fmtNumber(report.rows_dropped)} />
                      {report.skipped_duplicate && (
                        <p className="text-amber-600">Дубликат — пропущен</p>
                      )}
                    </>
                  )}
                  {job.error && <p className="text-destructive">{job.error}</p>}
                  {report?.warnings?.length ? (
                    <div className="pt-2 text-xs text-amber-600 space-y-1">
                      {report.warnings.map((w, i) => (
                        <p key={i}>⚠ {w}</p>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Обработка…</p>
              )}
            </>
          )}
        </div>
      </div>

      {/* Processing status summary */}
      {historyState === 'ready' && history.length > 0 && (() => {
        const counts: Record<string, number> = {}
        for (const d of history) counts[d.parse_status] = (counts[d.parse_status] ?? 0) + 1
        const tiles = [
          { label: 'Обработано', key: 'done', cls: 'text-green-700 bg-green-50 border-green-200' },
          { label: 'На проверке', key: 'needs_review', cls: 'text-blue-700 bg-blue-50 border-blue-200' },
          { label: 'Ошибки', key: 'error', cls: 'text-red-700 bg-red-50 border-red-200' },
          { label: 'Дубликаты', key: 'dup', cls: 'text-gray-600 bg-gray-50 border-gray-200' },
        ].filter((t) => (counts[t.key] ?? counts[t.key === 'done' ? 'ok' : t.key] ?? 0) > 0)
        if (!tiles.length) return null
        return (
          <div className="flex gap-3 flex-wrap">
            {tiles.map((t) => (
              <div key={t.key} className={`flex items-center gap-2 rounded-lg border px-4 py-2.5 ${t.cls}`}>
                <span className="text-xl font-bold tabular-nums">
                  {counts[t.key] ?? (t.key === 'done' ? (counts['ok'] ?? 0) : 0)}
                </span>
                <span className="text-sm">{t.label}</span>
              </div>
            ))}
          </div>
        )
      })()}

      {/* Upload history */}
      <div className="rounded-xl border border-border bg-card">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="text-base font-semibold text-foreground">История загрузок</h2>
        </div>
        {historyState === 'loading' ? (
          <Loading />
        ) : historyState === 'error' ? (
          <ErrorBox message="Не удалось загрузить историю" onRetry={loadHistory} />
        ) : history.length === 0 ? (
          <Empty label="Пока нет загруженных документов" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border">
                  {['Файл', 'Партнёр', 'Формат', 'Обработан', 'Статус'].map((h) => (
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
                {history.map((row) => (
                  <tr
                    key={row.doc_id}
                    className="border-b border-border last:border-0 hover:bg-muted/50"
                  >
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        <FileText
                          className={cn(
                            'h-4 w-4 shrink-0',
                            row.parse_status === 'error'
                              ? 'text-destructive'
                              : 'text-muted-foreground',
                          )}
                        />
                        <span className="text-sm text-foreground">{row.file_name}</span>
                      </div>
                    </td>
                    <td className="px-5 py-3 text-sm text-foreground">
                      {partners.get(row.partner_id) ?? '—'}
                    </td>
                    <td className="px-5 py-3 text-sm uppercase text-muted-foreground">
                      {row.file_format}
                    </td>
                    <td className="px-5 py-3 text-sm text-muted-foreground">
                      {fmtDateTime(row.parsed_at)}
                    </td>
                    <td className="px-5 py-3 text-sm text-foreground">
                      {statusLabel(row.parse_status)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function StatusLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className="text-foreground text-right">{value}</span>
    </div>
  )
}

function statusLabel(s: string): string {
  switch (s) {
    case 'queued':
      return 'В очереди'
    case 'running':
      return 'Обработка'
    case 'done':
      return 'Готово'
    case 'error':
      return 'Ошибка'
    case 'needs_review':
      return 'На проверке'
    case 'dup':
    case 'duplicate':
      return 'Дубликат'
    default:
      return s
  }
}
