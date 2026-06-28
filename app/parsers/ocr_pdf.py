"""OCR-based parser for true image-scanned PDFs (TZ 4.2).

Wraps app/ocr.py (EasyOCR ru) structured mode and adapts its output to a ParseResult,
so the ingest pipeline can treat an image scan exactly like any other source. Used only
when a PDF has no usable embedded text layer — text PDFs keep the faster geometric path.
"""

from __future__ import annotations

from pathlib import Path

from ..config import (
    OCR_CORRECT,
    OCR_CORRECT_METHOD,
    OCR_DPI,
    OCR_MAX_PAGES,
    OCR_MAX_SECONDS,
)
from .base import ParseResult, PriceRow


def parse_pdf_ocr(
    path: str | Path,
    max_pages: int | None = None,
    dpi: int | None = None,
    correct: bool | None = None,
) -> ParseResult:
    from ..ocr import ocr_pdf  # lazy: avoids loading EasyOCR/torch at import time

    data = ocr_pdf(
        path,
        dpi=dpi or OCR_DPI,
        max_pages=max_pages if max_pages is not None else OCR_MAX_PAGES,
        structured=True,
        max_seconds=OCR_MAX_SECONDS,
    )

    result = ParseResult(file_format="scan_pdf", raw_text=data.get("full_text", ""))
    for r in data.get("structured_rows", []):
        result.rows.append(
            PriceRow(
                name=r.get("name") or "(?)",
                code=r.get("code"),
                section=r.get("section"),
                prices=r.get("prices") or [],
            )
        )

    # Optional Groq/Speller post-correction of OCR'd Cyrillic service names (numbers are
    # left untouched). Opt-in via MEDARCHIVE_OCR_CORRECT; a no-op if the backend is absent.
    if correct is None:
        correct = OCR_CORRECT
    if correct and result.rows:
        from ..ocr_correction import correct_names

        before = [r.name for r in result.rows]
        fixed = correct_names(before, method=OCR_CORRECT_METHOD)
        changed = 0
        for row, new_name in zip(result.rows, fixed):
            if new_name and new_name != row.name:
                row.name = new_name
                changed += 1
        result.warnings.append(
            f"OCR name correction ({OCR_CORRECT_METHOD}): {changed}/{len(before)} repaired"
        )

    result.warnings.append(
        f"OCR engine={data.get('engine')}, pages={len(data.get('pages', []))}/"
        f"{data.get('pages_total')}, rows={len(result.rows)}, {data.get('elapsed_sec')}s"
    )
    if data.get("truncated"):
        result.warnings.append(
            f"OCR stopped at the {OCR_MAX_SECONDS:.0f}s budget — "
            f"only {len(data.get('pages', []))}/{data.get('pages_total')} pages processed"
        )
    if not result.rows:
        result.warnings.append("OCR produced no priced rows")
    return result
