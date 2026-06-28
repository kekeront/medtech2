"""Tesseract OCR engine (the ТЗ-named tool) — CPU-only, light, no GPU/heat.

Renders each PDF page and runs Tesseract (rus+eng) via pytesseract's word-level output,
then reuses the same column model + table.interpret_grid as the Surya path, so the two
engines are a fair head-to-head. Needs the system binary: `tesseract-ocr` + `tesseract-ocr-rus`.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

import fitz

from ..config import (
    GROQ_EXTRACT_MAX_PAGES,
    TESSERACT_DPI,
    TESSERACT_LANG,
    TESSERACT_MIN_CONF,
    TESSERACT_PSM,
)
from .base import ParseResult
from .columns import grid_from_items
from .table import interpret_grid

logger = logging.getLogger(__name__)


def tesseract_available() -> tuple[bool, str]:
    try:
        import pytesseract

        return (
            True,
            f"tesseract {pytesseract.get_tesseract_version()} ({TESSERACT_LANG})",
        )
    except Exception as exc:  # noqa: BLE001 — binary missing / pytesseract import error
        return False, f"tesseract unavailable: {type(exc).__name__}: {exc}"


def _render(page, dpi: int):
    from PIL import Image

    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
    return Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")


def _items(image) -> list[tuple]:
    """Tesseract word boxes → (x0, x1, top, text) items, dropping low-confidence noise."""
    import pytesseract
    from pytesseract import Output

    data = pytesseract.image_to_data(
        image,
        lang=TESSERACT_LANG,
        config=f"--psm {TESSERACT_PSM}",
        output_type=Output.DICT,
    )
    out: list[tuple] = []
    for i, text in enumerate(data["text"]):
        t = (text or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if t and conf >= TESSERACT_MIN_CONF:
            x0, top, w = data["left"][i], data["top"][i], data["width"][i]
            out.append((x0, x0 + w, top, t))
    return out


def parse_pdf_tesseract(path: str | Path, max_pages: int | None = None) -> ParseResult:
    doc = fitz.open(str(path))
    n_total = len(doc)
    limit = min(n_total, max_pages or GROQ_EXTRACT_MAX_PAGES)

    result = ParseResult(file_format="pdf")
    raw: list[str] = []
    for i in range(limit):
        img = _render(doc[i], TESSERACT_DPI)
        items = _items(img)
        raw.append(" ".join(t for *_, t in items))
        rows, labels = interpret_grid(grid_from_items(items, img.width))
        result.rows.extend(rows)
        if labels and not result.price_labels:
            result.price_labels = labels
    doc.close()

    result.raw_text = "\n".join(raw)
    result.warnings.append(
        f"tesseract ({TESSERACT_LANG}): {len(result.rows)} rows from {limit}/{n_total} pages"
    )
    if not result.rows:
        result.warnings.append("tesseract produced no rows")
    return result
