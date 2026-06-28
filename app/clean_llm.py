"""One-pass LLM data-quality cleanup of parsed rows before they reach the DB.

Runs once per document when Gemini is configured (ON by default; fail-open). It:
  - CLEANS text fields in place — service name, unit, section — fixing OCR garble, de-spaced
    words, ordinal/unit/section bleed and casing. Numbers, codes and the row count/order are
    NEVER touched, so a bad model response can't put a wrong price in the DB.
  - FLAGS suspicious rows (odd price, swapped tiers, header-as-row, probable duplicate, missing
    price) into ``row.issues`` with an ``LLM:`` prefix. The pipeline turns those into review
    reasons → they surface on the Проверка page for a human to confirm; nothing is auto-edited.

A failed/empty batch leaves its rows exactly as parsed.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from .config import (
    GEMINI_CLEAN,
    GEMINI_CLEAN_BATCH,
    GEMINI_CLEAN_WORKERS,
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_MODEL,
)
from .parsers.base import PriceRow
from .parsers.gemini_pdf import _client, gemini_available

logger = logging.getLogger(__name__)

# Marker prefix on row.issues so the pipeline can route only LLM flags into review reasons,
# and the Проверка page can filter on them.
LLM_FLAG_PREFIX = "LLM:"

_SYSTEM = (
    "You are a data-quality reviewer for Russian/Kazakh medical price lists. "
    "You clean TEXT fields only and flag suspicious rows. "
    "You NEVER change, add, or remove numbers, codes, or rows."
)

_PROMPT = """\
You are given parsed price-list ROWS as JSON. Return one cleaned object per row.

CLEAN these TEXT fields (return the corrected value, or the original if already fine):
  name     fix OCR damage and layout artifacts: re-join de-spaced words ("алл ергол ог"
           -> "аллерголог"), undo wrong splits, strip a leading № ordinal or a unit/section
           fragment that bled into the name, fix obvious casing. Keep the MEDICAL MEANING
           identical — do NOT translate, expand abbreviations, or invent words. If unsure,
           keep the original.
  unit     tidy the unit-of-measure text only.
  section  tidy the section-header text only.

FLAG suspicious rows — DO NOT fix numbers, only report. Short Russian strings in `flags`:
  - "цена подозрительная"  a price looks wrong (looks like a code/phone number, wildly off, 0)
  - "тарифы перепутаны"    resident vs non-resident prices look swapped
  - "строка-заголовок"     this is really a section/table header, not a priced service
  - "возможный дубль"      near-duplicate of another row in this batch
  - "нет цены"             a real service row that has no price at all
Leave `flags` empty for clean rows.

RULES:
- Return EXACTLY one object per input row, the SAME `i`, in the SAME ORDER, SAME COUNT.
- Output only the cleaned text fields and flags. Prices/codes are read-only context.
- Output strictly a JSON array, nothing else.

ROWS:
{rows_json}"""


class CleanedRow(BaseModel):
    i: int
    name: str | None = None
    unit: str | None = None
    section: str | None = None
    flags: list[str] = []


def _context(i: int, r: PriceRow) -> dict:
    """What the model needs to clean text and judge a row. Prices/code are read-only context."""
    return {
        "i": i,
        "name": r.name,
        "code": r.code,
        "unit": r.unit,
        "section": r.section,
        "resident": r.resident,
        "nonresident": r.nonresident,
        "prices": r.prices,
        "currency": r.currency,
    }


def _clean_batch(client, batch: list[tuple[int, PriceRow]]) -> dict[int, CleanedRow]:
    from google.genai import types

    rows_json = json.dumps([_context(i, r) for i, r in batch], ensure_ascii=False)
    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM,
        temperature=0,
        max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
        response_mime_type="application/json",
        response_schema=list[CleanedRow],
    )
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[_PROMPT.format(rows_json=rows_json)],
            config=config,
        )
    except Exception as exc:  # noqa: BLE001 — one batch failing must never block ingest
        logger.warning("gemini-clean batch failed: %s", exc)
        return {}
    parsed = resp.parsed
    if not parsed:
        try:
            parsed = [CleanedRow(**d) for d in json.loads(resp.text or "[]")]
        except Exception:  # noqa: BLE001
            return {}
    return {c.i: c for c in parsed}


def clean_rows(
    rows: list[PriceRow], force: bool = False
) -> tuple[list[PriceRow], list[str]]:
    """Clean text in place and flag suspicious rows. Returns (rows, warnings).

    The same row objects are returned (count and order preserved). Any batch that fails or
    returns nothing is left untouched (fail-open), so this can only ever improve or no-op.
    `force=True` runs even when GEMINI_CLEAN is off (used by the backfill script)."""
    if (not GEMINI_CLEAN and not force) or not rows:
        return rows, []
    ok, why = gemini_available()
    if not ok:
        return rows, [f"gemini-clean off: {why}"]

    indexed = list(enumerate(rows))
    step = max(1, GEMINI_CLEAN_BATCH)
    batches = [indexed[i : i + step] for i in range(0, len(indexed), step)]
    client = _client()
    with ThreadPoolExecutor(max_workers=max(1, GEMINI_CLEAN_WORKERS)) as pool:
        results = list(pool.map(lambda b: _clean_batch(client, b), batches))

    n_text = n_flag = n_failed = 0
    for batch, res in zip(batches, results):
        if not res:
            n_failed += 1
            continue
        for i, r in batch:
            c = res.get(i)
            if c is None:
                continue
            if c.name and c.name.strip() and c.name.strip() != r.name:
                r.name = c.name.strip()
                n_text += 1
            if c.unit is not None and (c.unit.strip() or None) != r.unit:
                r.unit = c.unit.strip() or None
            if c.section is not None and (c.section.strip() or None) != r.section:
                r.section = c.section.strip() or None
            for f in c.flags or []:
                f = (f or "").strip()
                if f:
                    r.issues.append(f"{LLM_FLAG_PREFIX} {f}")
                    n_flag += 1

    msgs = [
        f"gemini-clean: {n_text} names cleaned, {n_flag} rows flagged ({GEMINI_MODEL})"
    ]
    if n_failed:
        msgs.append(
            f"gemini-clean: {n_failed}/{len(batches)} batch(es) failed — rows kept as parsed"
        )
    return rows, msgs
