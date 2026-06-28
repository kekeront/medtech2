# Архитектура MedArchive

## Обзор

MedArchive состоит из трёх слоёв:

1. **Пайплайн инджестинга** — читает файлы любого формата, извлекает, нормализует и сохраняет данные
2. **REST API** — FastAPI-сервер, экспонирующий все данные и операции
3. **Фронтенд-дашборд** — Next.js-приложение для операторов

---

## Пайплайн инджестинга

```
Вход: file path / folder / ZIP
         │
         ▼
┌────────────────────────────┐
│     pipeline.ingest()      │  app/pipeline.py
│  - проверка дубликатов     │  sha256 → пропуск если уже есть
│  - копирование оригинала   │  → data/originals/{hash}__{name}
└────────────┬───────────────┘
             │
             ▼
┌────────────────────────────┐
│     detect_format()        │  app/parsers/detect.py
│  pdf / ocr_pdf / docx      │  по расширению + magic bytes
│  excel_xls / excel_xlsx    │  + эвристика по содержимому
└────────────┬───────────────┘
             │
    ┌─────────────────────┐
    │     Парсеры         │
    │  pdf.py             │  PyMuPDF → текстовый слой → таблицы
    │  ocr_pdf.py         │  EasyOCR/Surya/Tesseract → геометрические колонки
    │  docx.py            │  python-docx, принятие tracked changes
    │  excel.py           │  openpyxl/xlrd, автопоиск заголовочной строки
    └────────┬────────────┘
             │  List[RawRow]  {name, price_1, price_2, ...}
             ▼
┌────────────────────────────┐
│     tariffs.py             │  классификация колонок
│  - определение тарифных    │  (резидент, нерезидент, единая цена)
│    колонок по заголовку    │  
│  - FX-конвертация в KZT    │  fx.py → ЦБ РК / fallback-кеш
└────────────┬───────────────┘
             │  List[PriceItem]  {name, resident_kzt, nonresident_kzt}
             ▼
┌────────────────────────────┐
│     normalize.py           │  сопоставление с каталогом услуг
│  1. точное совпадение      │  services.service_name
│  2. синоним                │  services.synonyms[]
│  3. fuzzy (rapidfuzz)      │  WRatio ≥ threshold → service_id
│  нет совпадения            │  → service_id = NULL → /unmatched
└────────────┬───────────────┘
             │
             ▼
┌────────────────────────────┐
│     validation.py          │  проверки (ТЗ 4.4):
│  - price > 0               │  → needs_review = True + review_reason
│  - nonresident ≥ resident  │  строка не отбрасывается, сохраняется
│  - delta > 50% vs предыдущей версии
│  - дата не в будущем       │
│  - имя не пустое           │
└────────────┬───────────────┘
             │
             ▼
┌────────────────────────────┐
│     PostgreSQL             │  ORM: SQLAlchemy 2.0
│     (версионирование)      │  при новом документе с той же клиникой:
│                            │  старые позиции → is_active=False
└────────────────────────────┘
```

---

## Схема базы данных (ТЗ 3)

```
partners
  id, name, city, is_active

price_documents
  id, partner_id, filename, file_hash, effective_date,
  format, status, needs_review_count, created_at

price_items
  id, document_id, partner_id          -- денормализовано для скорости
  service_name_raw, service_id         -- NULL если не сопоставлено
  resident_price, nonresident_price    -- в KZT
  currency_original, fx_rate
  is_active, needs_review, review_reason, is_verified
  effective_date, created_at

services  (каталог)
  id, service_name, synonyms[], category, icd_code
```

### Версионирование

Цены никогда не перезаписываются. При загрузке нового прайса от той же клиники:
- Старые `price_items` для этого партнёра → `is_active = False`
- Новые позиции создаются с `is_active = True`
- История доступна через `?is_active=false` в API

---

## REST API

Сервер: FastAPI 0.138, ASGI uvicorn.

```
GET  /services          — каталог услуг
GET  /services/{id}/partners  — кто предоставляет + цены
GET  /partners          — партнёры (фильтр city, is_active)
GET  /partners/{id}/services  — прайс конкретного партнёра
GET  /search?q=         — полнотекстовый поиск
GET  /unmatched         — позиции без service_id
GET  /review            — очередь needs_review
POST /match             — ручная привязка к каталогу
POST /items/{id}/verify — верификация оператором
POST /upload            — загрузка файла (?ocr=true)
GET  /documents         — список документов
GET  /stats             — агрегированная статистика
POST /services/import   — загрузка каталога XLSX/JSON
POST /catalogue/bootstrap — синтез каталога из данных
POST /rematch           — перезапуск сопоставления
POST /ocr               — автономный OCR endpoint
```

Swagger UI: http://localhost:8000/docs

---

## Фронтенд-дашборд (Next.js)

```
frontend/
├── app/
│   ├── page.tsx              → /dashboard (редирект)
│   ├── dashboard/page.tsx    → Аналитика (stats + charts)
│   ├── search/page.tsx       → Поиск услуг
│   ├── partners/             → Список партнёров + детальная страница
│   ├── queue/page.tsx        → Очередь проверки (review + unmatched)
│   ├── upload/page.tsx       → Загрузка файлов с прогрессом
│   ├── documents/page.tsx    → Список документов
│   └── catalogue/page.tsx    → Каталог услуг
├── components/               → Переиспользуемые UI-компоненты
└── lib/
    └── api.ts               → Типизированный клиент API (mirrors app/schemas.py)
```

Dev-сервер проксирует `/api/*` → FastAPI на `:8000` (Next.js `rewrites` в `next.config.mjs`).

---

## OCR-пайплайн (скан-PDF)

Отсканированные PDF не имеют текстового слоя и табличной разметки, поэтому стандартный PDF-парсер бесполезен.

**Геометрическая реконструкция колонок (`app/parsers/ocr_pdf.py`):**

1. OCR-движок извлекает список `(text, x, y, w, h)` для каждого слова
2. Слова кластеризуются в строки по `y`-близости (порог = 0.5 * высота строки)
3. Внутри строки слова сортируются по `x`
4. Хвостовые числовые ячейки (справа) идентифицируются как цены
5. Крайняя левая нечисловая часть — название услуги

**Исправление OCR-артефактов (`app/parsers/numbers.py`):**
- Кириллические цифры: `О` → `0`, `З` → `3`, `б` → `6`
- Пробелы в числах: `12 000` → 12000
- Буквенные суффиксы: `80С` → 800 (С = ×10)
- Низкая уверенность → `needs_review = True`

---

## FX-конвертация (`app/fx.py`)

1. Запрос к API ЦБ РК (Национального банка Казахстана)
2. Fallback на `data/fx_last_good.json` при недоступности API
3. Конвертация: `price_kzt = price_original * fx_rate`
4. Курс сохраняется в `price_items.fx_rate` для аудита

---

## Конфигурация (`app/config.py`)

| Параметр | По умолчанию | Описание |
|----------|-------------|---------|
| `DATABASE_URL` | `postgresql://medarchive:medarchive@localhost/medarchive` | Postgres DSN |
| `OCR_BACKEND` | `easyocr` | `easyocr` / `surya` / `tesseract` |
| `FUZZY_THRESHOLD` | `80` | Порог rapidfuzz (0–100) |
| `PRICE_DELTA_THRESHOLD` | `0.5` | Порог флага при изменении цены >50% |
| `GROQ_API_KEY` | — | Ключ для LLM-парсинга (опционально) |
| `GOOGLE_API_KEY` | — | Ключ Google Gemini (опционально) |
