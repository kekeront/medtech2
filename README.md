# MedArchive — clinic price-list parser → structured Postgres

Parses partner-clinic price lists in **PDF, scanned-PDF, DOCX, XLS and XLSX**, extracts
`clinic → service → price` (resident / nonresident tariffs), validates and versions the
data, and serves it through a REST API with a small upload/search/review web UI.

Built for the *MedArchive* case (ТЗ Кейс 2). Scope, in priority order: **(1) parser quality
+ DB writing, (2) REST API, (3) error handling & manual fault-tolerance.**

---

## How it works

```
file ─▶ detect format ─▶ parse ─▶ map tariffs ─▶ FX→KZT ─▶ match catalogue ─▶ version ─▶ validate ─▶ Postgres
                          │                                                                   │
        pdf/scan: geometric column reconstruction              needs_review flag ◀────────────┘
        docx:     tables (tracked changes accepted)            (manual verification queue)
        xls/xlsx: every sheet, header auto-detected
```

- **Scanned PDFs** have a low-quality embedded OCR text layer and no table borders, so
  columns are reconstructed geometrically: words are clustered into rows by *y*, split into
  cells by *x*-gaps, and the trailing run of numeric cells is taken as the price columns.
  OCR digit corruption (`12 ООС`→12000, `II 000`→11000, `80С`→800) is repaired in
  `app/parsers/numbers.py`; low-confidence rows are flagged for review rather than dropped.
  A **separate, optional** OCR endpoint (`POST /ocr`) re-OCRs true image scans.
- **DOCX**: all tracked changes are accepted (insertions kept, deletions dropped) before
  tables are read.
- **XLS/XLSX**: walks every sheet, finds the header row (rarely row 0), classifies columns
  (code / name / unit / price-tiers) by keyword + numeric density.
- **Validation** (TZ 4.4): price > 0, nonresident ≥ resident, non-empty name, date not in
  future, >50 % change vs previous version, implausible-price detection → `needs_review`.
- **Versioning**: prices are never overwritten; a newer effective date supersedes the prior
  version (`is_active=false`), so price history is retained indefinitely.

## Schema (TZ 3)

`partners`, `price_documents`, `price_items`, `services` — see `app/models.py`.
`price_items` is denormalized with `partner_id` for query speed and carries
`needs_review` / `review_reason` / `is_verified` for the operator workflow.

---

## Setup

Requires Python 3.10+, [`uv`](https://docs.astral.sh/uv/), and a local PostgreSQL.

```bash
# 1. One-time DB role + database (peer auth via the postgres superuser)
sudo -u postgres psql -c "CREATE ROLE medarchive LOGIN PASSWORD 'medarchive' CREATEDB; \
                          CREATE DATABASE medarchive OWNER medarchive;"

# 2. Install deps
uv sync

# 3. Create the schema
uv run python -m app.cli init
```

Override the connection with `DATABASE_URL` (see `.env.example`).

## Ingest the archive

```bash
# a single file, a folder, or a .zip archive — all supported
uv run python -m app.cli ingest "/home/altairzhambyl/Downloads/Хакатон"
uv run python -m app.cli stats
```

Originals are copied into `data/originals/` and never deleted. Re-ingesting the same bytes
is skipped automatically (`--force` to override).

## Run the API + UI

```bash
uv run uvicorn app.api:app --reload --port 8000
```

- Web UI (upload / search / review / dashboard): http://localhost:8000/
- Swagger / OpenAPI: http://localhost:8000/docs

### Endpoints (TZ 4.5)

| Method | Path | Purpose |
|---|---|---|
| GET  | `/services?category=` | catalogue services |
| GET  | `/services/{id}/partners` | who offers a service + prices |
| GET  | `/partners?city=&is_active=` | partners |
| GET  | `/partners/{id}/services` | a partner's full price list |
| GET  | `/search?q=` | search services + partners + raw items |
| GET  | `/unmatched` | positions not yet matched to the catalogue |
| GET  | `/review` | rows flagged by validation |
| POST | `/match` | manually map items → a catalogue service |
| POST | `/items/{id}/verify` | confirm / correct a price item |
| POST | `/upload` | ingest one file via HTTP |
| GET  | `/documents`, `/stats` | processing status + quality dashboard |
| POST | `/ocr`, `GET /ocr/health` | standalone OCR for image scans (optional) |

## Reference catalogue

The target service catalogue is supplied by organizers (XLSX/JSON). Until loaded,
`service_id` stays `null` and every position lands in `/unmatched`. Create entries via
`POST /services`, then re-run matching (exact → synonym → fuzzy, threshold configurable).

## Project layout

```
app/
  config.py db.py models.py schemas.py     # config, DB, ORM, API models
  parsers/  numbers detect table pdf docx excel registry   # extraction
  tariffs.py normalize.py validation.py    # tariff mapping, catalogue match, checks
  pipeline.py cli.py api.py ocr.py ocr_api.py
  static/index.html                        # web UI
```
