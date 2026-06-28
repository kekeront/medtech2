"""Gemini (Vertex AI) PDF extraction — fast, Sonnet-grade structured extraction.

Sends page-range chunks of the PDF straight to gemini-2.5-flash, which does OCR + layout +
table-structure + tier mapping in a single shot (no local OCR, no GPU). Chunks are kept SMALL
and run with high parallelism because OUTPUT tokens — not input — are the real ceiling on long
(85-page) price lists. Structured output (`response_schema`) returns rows already in our shape
with resident / non-resident / extra tiers resolved, so they reach the pipeline with
`tariffs_resolved=True` (no positional guessing).

The prompt is deliberately rigorous (the same contract the hand-built golden transcriptions
used): capture EVERY priced row, map tiers from the column headers, keep names verbatim, and —
critically — NEVER invent a number. An unreadable price becomes null + a note, not a guess.

Auth: Application Default Credentials (gcloud auth application-default login, or a service
account via GOOGLE_APPLICATION_CREDENTIALS) + GOOGLE_CLOUD_PROJECT.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fitz
from pydantic import BaseModel

from ..config import (
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_MAX_PAGES,
    GEMINI_MAX_RETRIES,
    GEMINI_MODEL,
    GEMINI_PAGES_PER_CALL,
    GEMINI_RETRY_WAIT,
    GEMINI_THINKING_BUDGET,
    GEMINI_TIMEOUT_MS,
    GEMINI_WORKERS,
    VERTEX_LOCATION,
    VERTEX_PROJECT,
)
from .base import ParseResult
from .pdf_groq import _to_row

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a meticulous medical price-list transcriber for Russian/Kazakh clinics. "
    "Capture every priced row exactly; copy text verbatim; never fabricate a number."
)

_PROMPT = """\
Transcribe a Russian/Kazakh medical clinic PRICE LIST into ground-truth rows. This is PDF
pages {start}–{end} of the document. Extract EVERY priced service row — do not sample, skip,
summarise, or stop early. A dense page may hold 30–45 rows.

Per service row return:
  name        service name VERBATIM (join wrapped lines into one; do NOT fix spelling/typos;
              drop only a leading № ordinal and the unit)
  code        service/tariff code if a code column exists ("U1.1","B02.110.002","DR3.4"), else null
  unit        unit of measure if a column exists ("1 посещение","услуга", or a specimen "кровь с ЭДТА"), else null
  section     the heading currently in effect — a full-width "Раздел 3.Дерматовенерология", a
              coloured "Блок …"/numbered subsection, or an ALL-CAPS lab header. For a row that
              continues a section begun on an earlier page, repeat that section.
  price_resident_kzt     residents / граждане РК / местный / первичный приём
  price_nonresident_kzt  non-residents / иностранцы / дальнее зарубежье / повторный приём
  extra_tiers            list of {{label, price_kzt}} for ANY price tier BEYOND the two above. Use a
                         SHORT CANONICAL label, NOT the full column header: "страховая" for insurance /
                         страховых компаний, "СНГ" for ближнее зарубежье, "партнёр" for partner / со
                         партнёра. Empty list if none.
  currency    "KZT" (default), "RUB", or "USD" — the number as shown, never converted
  note        "пакет" for a package/CHECK-UP total row; the range/"от"/coefficient rule for a
              non-fixed price; "unreadable" for an illegible price; else null
  page        the actual PDF page number this row appears on

TIER MAPPING — read the COLUMN HEADERS; never assume the first column is resident:
- ONE price column → put it under whichever tier the header names (often non-resident), other null.
- первичный/повторный приём as two columns → resident / non-resident respectively.
- 3-tier table like Клиника 2 (страховых компаний РК | резидентов РК ближнее зарубежье |
  нерезидентов РК дальнее зарубежье): price_resident_kzt = the РЕЗИДЕНТ/ближнее column,
  price_nonresident_kzt = the НЕРЕЗИДЕНТ/дальнее column, extra_tiers = [{{"страховая": страховых value}}].
  Never put the резидент value in extra_tiers and never leave price_resident_kzt empty when that column has a number.
- 3-tier table like Клиника 4 (граждане РК | СНГ | дальнее зарубежье): price_resident_kzt = граждане РК,
  price_nonresident_kzt = дальнее, extra_tiers = [{{"СНГ": СНГ value}}].

ROWS vs HEADERS:
- A package/CHECK-UP shows a bold TOTAL row (emit it, note="пакет") followed by its component
  services (emit each as its own row). Keep all of them.
- Section titles, the column-header row, "Приложение …"/contract headers, page numbers, totals
  and blank rows are NOT service rows — use section titles to fill `section`; skip the rest.

NUMBERS — accuracy is critical:
- Read digits EXACTLY; strip spaces inside a number ("16 600" → 16600). A printed "0" → 0.
- A range ("7000–20000","От 5000") → store the LOWER bound, record the range in note.
- A multiplier/reference ("коэфф 2","цена+20% kdlolymp.kz","договорная") → price null, rule in note.
- If a digit is genuinely unreadable (faint/cropped) → that price = null, note="unreadable".
  NEVER guess or invent a price."""


class _Tier(BaseModel):
    label: str
    price_kzt: float


class GeminiRow(BaseModel):
    name: str
    code: str | None = None
    unit: str | None = None
    section: str | None = None
    price_resident_kzt: float | None = None
    price_nonresident_kzt: float | None = None
    extra_tiers: list[_Tier] | None = None
    currency: str = "KZT"
    note: str | None = None
    page: int | None = None


def gemini_available() -> tuple[bool, str]:
    try:
        import google.genai  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"google-genai not importable: {exc}"
    if not VERTEX_PROJECT:
        return False, "GOOGLE_CLOUD_PROJECT not set"
    return True, f"vertex:{GEMINI_MODEL}@{VERTEX_LOCATION}"


# One shared, thread-safe client (a per-chunk client gets closed mid-pool).
_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        from google import genai
        from google.genai import types

        _CLIENT = genai.Client(
            vertexai=True,
            project=VERTEX_PROJECT,
            location=VERTEX_LOCATION,
            http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
        )
    return _CLIENT


def _chunk_bytes(src: fitz.Document, start: int, end: int) -> bytes:
    sub = fitz.open()
    sub.insert_pdf(src, from_page=start, to_page=end - 1)
    data = sub.tobytes()
    sub.close()
    return data


# Substrings that mark a transient, retryable failure (rate limit / 5xx / timeout).
_RETRYABLE = (
    "429",
    "500",
    "503",
    "504",
    "resource_exhausted",
    "unavailable",
    "deadline",
    "timeout",
    "overloaded",
)


def _extract_chunk(client, pdf_bytes: bytes, start: int, end: int) -> list[dict]:
    from google.genai import types

    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM,
        temperature=0,
        max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
        response_mime_type="application/json",
        response_schema=list[GeminiRow],
        thinking_config=types.ThinkingConfig(thinking_budget=GEMINI_THINKING_BUDGET),
    )
    contents = [
        _PROMPT.format(start=start + 1, end=end),
        types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
    ]
    resp = None
    for attempt in range(GEMINI_MAX_RETRIES + 1):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL, contents=contents, config=config
            )
            break
        except Exception as exc:  # noqa: BLE001 — one chunk must never abort the document
            retryable = any(t in str(exc).lower() for t in _RETRYABLE)
            if attempt >= GEMINI_MAX_RETRIES or not retryable:
                logger.warning("Gemini chunk p%d–%d failed: %s", start + 1, end, exc)
                return []
            wait = GEMINI_RETRY_WAIT * (attempt + 1)
            logger.warning(
                "Gemini chunk p%d–%d %s — retry %d/%d in %.0fs",
                start + 1,
                end,
                type(exc).__name__,
                attempt + 1,
                GEMINI_MAX_RETRIES,
                wait,
            )
            time.sleep(wait)

    if resp is None:
        return []
    parsed = resp.parsed
    if parsed:
        return [r.model_dump() for r in parsed]
    import json

    try:
        data = json.loads(resp.text or "[]")
        return data if isinstance(data, list) else data.get("rows", [])
    except (json.JSONDecodeError, AttributeError):
        return []


# Canonical tier labels — models echo the full (and OCR-garbled, inconsistent) column header,
# so collapse each to one short, stable key. Order matters: most specific first.
_TIER_CANON: tuple[tuple[str, re.Pattern], ...] = (
    ("страховая", re.compile(r"страхов", re.I)),
    ("нерезидент", re.compile(r"нерезидент|дальн", re.I)),
    ("резидент", re.compile(r"резидент|ближн", re.I)),
    ("СНГ", re.compile(r"\bснг\b", re.I)),
    ("партнёр", re.compile(r"партн", re.I)),
)


def _canon_tier(label: str) -> str:
    for canon, pat in _TIER_CANON:
        if pat.search(label):
            return canon
    return label.strip()[:48]


def _adapt(d: dict) -> dict:
    """A GeminiRow dict -> the shape _to_row expects. Collapses the extra_tiers list into a
    price_extra_tiers {label: value} map with CANONICAL labels, and — crucially — re-routes a
    tier the model mislabeled as резидент/нерезидент back into its proper price slot (the model
    occasionally drops the resident column into extra_tiers on 3-tier tables like Клиника 2).
    `page`/`note` stay in the dict but are ignored downstream by _to_row (provenance only)."""
    tiers = d.pop("extra_tiers", None) or []
    extra: dict[str, float] = {}
    for t in tiers:
        if not isinstance(t, dict) or not t.get("label"):
            continue
        val = t.get("price_kzt")
        if val is None:
            continue
        canon = _canon_tier(str(t["label"]))
        if canon == "резидент" and d.get("price_resident_kzt") is None:
            d["price_resident_kzt"] = val  # mislabeled resident column -> its real slot
        elif canon == "нерезидент" and d.get("price_nonresident_kzt") is None:
            d["price_nonresident_kzt"] = val
        else:
            extra[canon] = val
    d["price_extra_tiers"] = extra or None
    return d


def parse_pdf_gemini(path: str | Path, max_pages: int | None = None) -> ParseResult:
    doc = fitz.open(str(path))
    n_total = len(doc)
    limit = min(n_total, max_pages or GEMINI_MAX_PAGES)
    step = max(1, GEMINI_PAGES_PER_CALL)
    ranges = [(i, min(i + step, limit)) for i in range(0, limit, step)]
    payloads = [(_chunk_bytes(doc, s, e), s, e) for s, e in ranges]
    doc.close()

    client = _client()  # created in the main thread, shared across chunks
    with ThreadPoolExecutor(max_workers=max(1, GEMINI_WORKERS)) as pool:
        per_chunk = list(pool.map(lambda p: _extract_chunk(client, *p), payloads))

    result = ParseResult(file_format="pdf")
    for rows in per_chunk:
        for d in rows:
            row = _to_row(_adapt(d))
            if row is not None:
                result.rows.append(row)

    result.warnings.append(
        f"gemini ({GEMINI_MODEL}): {len(result.rows)} rows from {limit}/{n_total} pages "
        f"in {len(ranges)} chunk(s) × {GEMINI_WORKERS} workers, "
        f"thinking={GEMINI_THINKING_BUDGET}"
    )
    if not result.rows:
        result.warnings.append("gemini produced no rows")
    return result
