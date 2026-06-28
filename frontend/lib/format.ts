// Shared display formatters. Data is stored in KZT (₸).

export function fmtKzt(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${Math.round(v).toLocaleString('ru-RU')} ₸`
}

export function fmtNumber(v: number | null | undefined): string {
  if (v == null) return '—'
  return v.toLocaleString('ru-RU')
}

// Backend dates arrive as ISO ("2024-05-20" or full datetime). Show DD.MM.YYYY.
export function fmtDate(d: string | null | undefined): string {
  if (!d) return '—'
  const datePart = d.split('T')[0]
  const [y, m, day] = datePart.split('-')
  if (!y || !m || !day) return datePart
  return `${day}.${m}.${y}`
}

export function fmtDateTime(d: string | null | undefined): string {
  if (!d) return '—'
  const [datePart, timePart] = d.split('T')
  const date = fmtDate(datePart)
  if (!timePart) return date
  return `${date} ${timePart.slice(0, 5)}`
}

export function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${v.toFixed(1)}%`
}
