"""LLM-based PDF extraction via Groq (OSS models).

For PDFs the geometric / pdfplumber parsers can't handle — wrapped names, multi-tier
tariffs, scrambled OCR text layers — we hand each page's text to a Groq-hosted OSS model
and ask for structured rows directly. The model resolves resident / non-resident / extra
tariffs from the column headers itself, so the row arrives with `tariffs_resolved=True`
and skips the positional guesser in the pipeline.

Text path:   GROQ_EXTRACT_MODEL over the embedded text layer (cheap; for clean PDFs).
Vision path: GROQ_VISION_MODEL (Qwen3-VL) over rendered page images — for documents whose
             text layer is garbled/absent. Pick with `vision=True`.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fitz

from ..config import (
    GROQ_EXTRACT_MAX_PAGES,
    GROQ_EXTRACT_MODEL,
    GROQ_EXTRACT_WORKERS,
    GROQ_MAX_RETRIES,
    GROQ_PAGES_PER_CALL,
    GROQ_RATE_LIMIT_WAIT,
    GROQ_VISION_MODEL,
)
from .base import ParseResult, PriceRow

logger = logging.getLogger(__name__)
_MAX_CHARS = 24000  # prompt text cap (a few pages of price-list text)
_VISION_DPI = 150  # render resolution for vision pages (lower = fewer tokens)

_SYSTEM = (
    "You extract medical price-list rows from Russian clinic documents. "
    "Return ONLY valid JSON. Never invent data; copy values verbatim from the text. "
    "/no_think"  # Qwen3 is a reasoning model — disable thinking so json_object mode is clean
)

_PROMPT = """\
Below is the raw text of one or more pages of a Russian clinic price list. Extract every
priced service row. Return JSON: {"rows": [ {...} ]} where each row has:
  name                  service name, verbatim, WITHOUT the leading № ordinal or the unit
  code                  service/tariff code if present, else null
  unit                  unit of measure (e.g. "1 посещение", "услуга") if present, else null
  section               the section/category heading this row falls under, else null
  price_resident_kzt    price for residents / граждане РК / local; null if the column is absent
  price_nonresident_kzt price for non-residents / иностранцы / не проживающие; null if absent
  price_extra_tiers     object {label: number} for any FURTHER tier (дальнее зарубежье,
                        страховая, скидка), else null
  currency              "KZT", "RUB", or "USD" (number as shown; do not convert)

Rules:
- Read column headers to decide which price is resident vs non-resident. If there is ONE
  price column, put it under whichever the header names (often non-resident here); the
  other stays null. Never assume the first column is resident.
- Skip header rows, section titles with no price, totals, and footnotes.
- Keep numbers exactly, stripping spaces inside them ("10 800" -> 10800).
- REPAIR OCR digit corruption in prices: Cyrillic/Latin look-alikes stand in for digits —
  О о С с O o -> 0, З з -> 3, б -> 6, В B -> 8, І l | -> 1, Ѕ S -> 5. So "10 80С" -> 10800,
  "12 ООС" -> 12000, "9 60С" -> 9600. Output the repaired integer.
- Only use null when there is genuinely no price for that row at all.

PAGE TEXT:
<<TEXT>>"""

# Vision variant: same rules, but the page arrives as an image instead of text.
_PROMPT_VISION = _PROMPT.replace(
    "PAGE TEXT:\n<<TEXT>>",
    "The attached image is one page of the price list. Extract its rows.",
)


def _client():
    from groq import Groq

    # Disable the SDK's own retry — we handle 429s ourselves, waiting out the full
    # per-minute window (see _create) instead of the SDK's shorter exponential backoff.
    return Groq(max_retries=0)


def _retry_after(exc) -> float | None:
    """Seconds to wait from a 429's Retry-After header, if present."""
    try:
        ra = exc.response.headers.get("retry-after")
        return float(ra) if ra else None
    except Exception:  # noqa: BLE001
        return None


def _create(client, **kwargs):
    """chat.completions.create with per-minute rate-limit waiting on 429."""
    from groq import RateLimitError

    for attempt in range(GROQ_MAX_RETRIES + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError as exc:
            if attempt >= GROQ_MAX_RETRIES:
                raise
            wait = _retry_after(exc) or GROQ_RATE_LIMIT_WAIT
            logger.warning(
                "Groq 429 (per-minute limit) — waiting %.0fs, retry %d/%d",
                wait,
                attempt + 1,
                GROQ_MAX_RETRIES,
            )
            time.sleep(wait)


def _messages(text: str | None, b64: str | None) -> list[dict]:
    if b64 is not None:
        user: object = [
            {"type": "text", "text": _PROMPT_VISION},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]
    else:
        user = _PROMPT.replace("<<TEXT>>", (text or "")[:_MAX_CHARS])
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def _call(model: str, messages: list[dict], json_mode: bool) -> str:
    kwargs: dict = {"response_format": {"type": "json_object"}} if json_mode else {}
    # Qwen3 is a reasoning model; without this it spends the whole token budget "thinking"
    # and never emits the JSON (and thinking even breaks json_object mode with a 400).
    if "qwen" in model.lower():
        kwargs["reasoning_effort"] = "none"
    resp = _create(_client(), model=model, temperature=0, messages=messages, **kwargs)
    return resp.choices[0].message.content or ""


def _parse_rows(content: str) -> list[dict]:
    # Reasoning models (Qwen3) emit <think>…</think> before the answer; that block holds
    # brace characters that derail JSON extraction, so drop everything up to the last close.
    cut = content.rfind("</think>")
    if cut != -1:
        content = content[cut + len("</think>") :]
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end <= start:
            return []
        try:
            data = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return []
    rows = data.get("rows", data) if isinstance(data, dict) else data
    return rows if isinstance(rows, list) else []


def _extract(
    model: str, *, text: str | None = None, b64: str | None = None
) -> list[dict]:
    """Extract rows from one text batch or one page image. Best-effort: any failure
    returns [] (never aborts the document); retries plain if strict json_object is rejected."""
    if text is not None and not text.strip():
        return []
    messages = _messages(text, b64)
    try:
        content = _call(model, messages, json_mode=True)
    except Exception as exc:  # noqa: BLE001 — incl. Groq json_validate_failed (400)
        logger.warning("Groq json-mode failed (%s); retrying plain", type(exc).__name__)
        try:
            content = _call(model, messages, json_mode=False)
        except Exception as exc2:  # noqa: BLE001
            logger.warning("Groq extraction failed: %s", exc2)
            return []
    return _parse_rows(content)


def _render_png(page) -> str:
    pix = page.get_pixmap(matrix=fitz.Matrix(_VISION_DPI / 72, _VISION_DPI / 72))
    return base64.b64encode(pix.tobytes("png")).decode()


def _to_row(d: dict) -> PriceRow | None:
    name = (d.get("name") or "").strip()
    if not name:
        return None
    res = _num(d.get("price_resident_kzt"))
    non = _num(d.get("price_nonresident_kzt"))
    extra = {
        str(k): _num(v)
        for k, v in (d.get("price_extra_tiers") or {}).items()
        if _num(v) is not None
    } or None
    return PriceRow(
        name=name,
        code=(str(d["code"]).strip() if d.get("code") else None),
        unit=(str(d["unit"]).strip() if d.get("unit") else None),
        section=(str(d["section"]).strip() if d.get("section") else None),
        currency=(d.get("currency") or "KZT").upper(),
        raw=name,
        tariffs_resolved=True,
        resident=res,
        nonresident=non,
        extra_tiers=extra,
    )


def _num(v) -> float | None:
    """Coerce a model-emitted price to a float, repairing any residual OCR look-alikes
    (e.g. the model echoing "10 80С") via the aggressive numeric parser."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    from .numbers import parse_price

    val, _ = parse_price(str(v), aggressive=True)
    return val


def parse_pdf_groq(
    path: str | Path, max_pages: int | None = None, vision: bool = False
) -> ParseResult:
    """Extract price rows from a PDF via Groq. vision=True renders pages and uses the
    Qwen3-VL model (for garbled/absent text layers); otherwise the text layer is used."""
    doc = fitz.open(str(path))
    n_total = len(doc)
    limit = min(n_total, max_pages or GROQ_EXTRACT_MAX_PAGES)

    if vision:
        model = GROQ_VISION_MODEL
        imgs = [_render_png(doc[i]) for i in range(limit)]
        doc.close()
        result = ParseResult(file_format="pdf")
        units, fn = imgs, (lambda b: _extract(model, b64=b))  # one image per call
    else:
        model = GROQ_EXTRACT_MODEL
        page_texts = [doc[i].get_text("text") for i in range(limit)]
        doc.close()
        # Batch several pages per call to stay under the request-per-minute ceiling.
        step = max(1, GROQ_PAGES_PER_CALL)
        units = [
            "\n\n".join(page_texts[i : i + step])
            for i in range(0, len(page_texts), step)
        ]
        result = ParseResult(file_format="pdf", raw_text="\n\n".join(page_texts))
        fn = lambda t: _extract(model, text=t)  # noqa: E731

    with ThreadPoolExecutor(max_workers=max(1, GROQ_EXTRACT_WORKERS)) as pool:
        per_unit = list(pool.map(fn, units))

    for rows in per_unit:
        for d in rows:
            row = _to_row(d)
            if row is not None:
                result.rows.append(row)

    mode = "vision" if vision else "text"
    result.warnings.append(
        f"groq {mode} extraction ({model}): {len(result.rows)} rows "
        f"from {limit}/{n_total} pages in {len(units)} call(s)"
    )
    if not result.rows:
        result.warnings.append("groq extraction produced no rows")
    return result
