// Typed client for the MedArchive FastAPI backend.
//
// All requests go to /api/* which next.config.mjs rewrites to the backend
// (http://localhost:8000 by default), so everything is same-origin in the browser.

const BASE = '/api'

// ----------------------------------------------------------------- backend types
// Mirror app/schemas.py — keep in sync if the backend contract changes.

export interface Partner {
  partner_id: string
  name: string
  city: string | null
  address: string | null
  contact_email: string | null
  contact_phone: string | null
  is_active: boolean
}

export interface Service {
  service_id: string
  service_name: string
  category: string | null
  synonyms: string[]
  icd_code: string | null
  is_active: boolean
}

export interface PriceItem {
  item_id: string
  partner_id: string
  partner_name: string | null
  doc_id: string
  service_id: string | null
  service_name_raw: string
  service_code_source: string | null
  section: string | null
  unit: string | null
  price_resident_kzt: number | null
  price_nonresident_kzt: number | null
  price_extra_tiers: Record<string, number> | null
  price_original: number | null
  currency_original: string
  match_method: string | null
  match_confidence: number | null
  needs_review: boolean
  review_reason: string | null
  is_verified: boolean
  effective_date: string | null
  is_active: boolean
  merged_count: number
  merged_codes: string[] | null
}

export interface PriceDocument {
  doc_id: string
  partner_id: string
  file_name: string
  file_format: string
  effective_date: string | null
  parsed_at: string | null
  parse_status: string
  parse_log: string | null
}

export interface PriceHistoryPoint {
  effective_date: string
  year: number
  price_resident_kzt: number | null
  price_nonresident_kzt: number | null
}

export interface ItemPriceHistory {
  item_id: string
  service_name: string
  service_code_source: string | null
  matched_by: string
  points: PriceHistoryPoint[]
}

export interface Stats {
  documents: number
  documents_by_status: Record<string, number>
  partners: number
  services: number
  price_items: number
  auto_matched: number
  match_rate_pct: number
  needs_review: number
}

export interface SearchResult {
  partners: Partner[]
  services: Service[]
  price_items: PriceItem[]
}

export interface UploadAck {
  job_id: string
  status_url: string
  status: string
}

export interface IngestReport {
  file_name: string
  doc_id: string | null
  partner: string | null
  file_format: string | null
  status: string
  rows_parsed: number
  rows_written: number
  rows_dropped: number
  rows_needs_review: number
  rows_matched: number
  warnings: string[]
  skipped_duplicate: boolean
}

export interface JobStatus {
  job_id: string
  archive: string
  status: 'queued' | 'running' | 'done' | 'error'
  total: number
  done: number
  current: string | null
  files: number
  errors: number
  rows_written: number
  rows_needs_review: number
  elapsed_sec: number
  error: string | null
  reports: IngestReport[]
}

// ----------------------------------------------------------------- fetch helpers

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { Accept: 'application/json' },
      ...init,
    })
  } catch (e) {
    throw new ApiError(0, 'Не удалось связаться с сервером. Запущен ли бэкенд?')
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') p.set(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

// ----------------------------------------------------------------- endpoints

export const api = {
  stats: () => request<Stats>('/stats'),

  search: (q: string, limit = 50) =>
    request<SearchResult>(`/search${qs({ q, limit })}`),

  listPartners: (opts: { city?: string; is_active?: boolean; limit?: number } = {}) =>
    request<Partner[]>(`/partners${qs({ limit: 500, ...opts })}`),

  getPartner: (id: string) => request<Partner>(`/partners/${id}`),

  partnerServices: (id: string, opts: { aggregate?: boolean; limit?: number } = {}) =>
    request<PriceItem[]>(`/partners/${id}/services${qs({ limit: 5000, ...opts })}`),

  listDocuments: (opts: { status?: string; limit?: number } = {}) =>
    request<PriceDocument[]>(`/documents${qs(opts)}`),

  deleteDocument: (docId: string, opts: { purge_original?: boolean } = {}) =>
    request<{
      deleted: string
      file_name: string
      items_deleted: number
      original_purged: boolean
    }>(`/documents/${docId}${qs(opts)}`, { method: 'DELETE' }),

  listServices: (opts: { active?: boolean; limit?: number } = {}) =>
    request<Service[]>(`/services${qs({ limit: 1000, ...opts })}`),

  review: (limit = 500) => request<PriceItem[]>(`/review${qs({ limit })}`),

  unmatched: (limit = 500) => request<PriceItem[]>(`/unmatched${qs({ limit })}`),

  /** Rows the LLM cleanup flagged as suspicious, awaiting human confirmation. */
  flagged: (limit = 500) => request<PriceItem[]>(`/flagged${qs({ limit })}`),

  verifyItem: (
    itemId: string,
    body: {
      is_verified?: boolean
      price_resident_kzt?: number
      price_nonresident_kzt?: number
      note?: string
    },
  ) =>
    request<PriceItem>(`/items/${itemId}/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_verified: true, ...body }),
    }),

  upload: (file: File, opts: { force?: boolean; ocr?: boolean } = {}) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<UploadAck>(`/upload${qs(opts)}`, { method: 'POST', body: fd })
  },

  job: (jobId: string) => request<JobStatus>(`/jobs/${jobId}`),

  documentItems: (docId: string) =>
    request<PriceItem[]>(`/documents/${docId}/items`),

  itemHistory: (itemId: string) =>
    request<ItemPriceHistory>(`/items/${itemId}/history`),

  deleteItem: (itemId: string) =>
    request<void>(`/items/${itemId}`, { method: 'DELETE' }),

  createDocumentItem: (
    docId: string,
    body: {
      service_name_raw: string
      section?: string | null
      price_resident_kzt?: number | null
      price_nonresident_kzt?: number | null
      effective_date?: string | null
    },
  ) =>
    request<PriceItem>(`/documents/${docId}/items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  patchItem: (
    itemId: string,
    body: {
      service_name_raw?: string
      section?: string | null
      price_resident_kzt?: number | null
      price_nonresident_kzt?: number | null
      effective_date?: string | null
    },
  ) =>
    request<PriceItem>(`/items/${itemId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  /** URL for the raw original file — use in <a href download> */
  documentFileUrl: (docId: string) => `${BASE}/documents/${docId}/file`,

  /** URL for the in-browser preview (PDF inline, xlsx/xls→HTML table, docx→HTML) */
  documentPreviewUrl: (docId: string, sheet = 0) =>
    `${BASE}/documents/${docId}/preview${sheet ? `?sheet=${sheet}` : ''}`,
}
