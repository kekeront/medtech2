// Buckets for the review queue. The backend flags rows with a free-text
// `review_reason`; we classify those into a few operator-facing groups, plus a
// separate "unmatched" group sourced from /unmatched (items with no catalogue match).

export type Priority = 'high' | 'medium' | 'low'

export interface QueueBucket {
  key: string
  label: string
  description: string
  priority: Priority
  match: RegExp
  /** 'unmatched' is sourced from /unmatched; the rest from /review by reason. */
  source: 'review' | 'unmatched'
}

// Order matters: first matching bucket wins; the catch-all ('other') must be last
// among the review buckets.
export const QUEUE_BUCKETS: QueueBucket[] = [
  {
    key: 'price',
    label: 'Проблемы с ценой',
    description: 'Цена нулевая, неправдоподобная или нерезидент < резидент',
    priority: 'high',
    match: /цен|price|резидент|resident|nonresident|implausible|тариф/i,
    source: 'review',
  },
  {
    key: 'name',
    label: 'Проблемы с наименованием',
    description: 'Пустое или подозрительное наименование услуги',
    priority: 'medium',
    match: /наименован|назван|name|пуст|empty/i,
    source: 'review',
  },
  {
    key: 'change',
    label: 'Резкое изменение цены',
    description: 'Цена изменилась более чем на 50 % к прошлой версии',
    priority: 'medium',
    match: /измен|change|%|previous|верси/i,
    source: 'review',
  },
  {
    key: 'other',
    label: 'Прочее на проверке',
    description: 'Другие причины, отмеченные при разборе',
    priority: 'low',
    match: /.*/,
    source: 'review',
  },
  {
    key: 'unmatched',
    label: 'Несопоставлено со справочником',
    description: 'Услуги не найдены в каталоге услуг',
    priority: 'low',
    match: /.*/,
    source: 'unmatched',
  },
]

export function bucketOf(key: string): QueueBucket | undefined {
  return QUEUE_BUCKETS.find((b) => b.key === key)
}

// Classify a review item's reason into one of the 'review'-source bucket keys.
export function classifyReason(reason: string | null): string {
  const r = reason ?? ''
  for (const b of QUEUE_BUCKETS) {
    if (b.source === 'review' && b.match.test(r)) return b.key
  }
  return 'other'
}

export const PRIORITY_META: Record<
  Priority,
  { label: string; text: string; bar: string; border: string }
> = {
  high: {
    label: 'Высокая приоритетность',
    text: 'text-red-600',
    bar: 'bg-red-400',
    border: 'border-l-red-400',
  },
  medium: {
    label: 'Средняя приоритетность',
    text: 'text-amber-600',
    bar: 'bg-amber-400',
    border: 'border-l-amber-400',
  },
  low: {
    label: 'Низкая приоритетность',
    text: 'text-green-600',
    bar: 'bg-green-400',
    border: 'border-l-green-400',
  },
}
