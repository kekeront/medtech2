"""Self-hosted OCR via Surya — light (650M) multilingual engine incl. Russian.

A cooler, faster alternative to a 3B VLM for reading garbled/scanned PDFs: Surya gives
clean text lines with word-level positions, which we bin into a column grid
(parsers.columns) and structure with table.interpret_grid. Runs on the GPU (cap power to
bound heat) or CPU (SURYA_DEVICE=cpu); small batch sizes keep VRAM/heat modest.

Predictors are heavy to construct, so they're loaded once and cached.
"""

from __future__ import annotations

import logging
import os
from io import BytesIO
from pathlib import Path

import fitz

from ..config import (
    GROQ_EXTRACT_MAX_PAGES,
    SURYA_DET_BATCH,
    SURYA_DEVICE,
    SURYA_DPI,
    SURYA_REC_BATCH,
)
from .base import ParseResult
from .columns import grid_from_items
from .table import interpret_grid

logger = logging.getLogger(__name__)

_PRED = None  # (recognition_predictor, detection_predictor) — lazily loaded singletons


def surya_available() -> tuple[bool, str]:
    try:
        import surya  # noqa: F401

        return True, f"surya ({SURYA_DEVICE})"
    except Exception as exc:  # noqa: BLE001
        return False, f"surya unavailable: {type(exc).__name__}: {exc}"


def _predictors():
    global _PRED
    if _PRED is None:
        # Surya reads the device from TORCH_DEVICE; set it before constructing predictors.
        os.environ.setdefault("TORCH_DEVICE", SURYA_DEVICE)
        from surya.detection import DetectionPredictor
        from surya.foundation import FoundationPredictor
        from surya.recognition import RecognitionPredictor

        foundation = FoundationPredictor()
        _PRED = (RecognitionPredictor(foundation), DetectionPredictor())
    return _PRED


def _render(page, dpi: int):
    from PIL import Image

    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
    return Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")


def _items(result) -> list[tuple]:
    """Flatten a Surya OCRResult into (x0, x1, top, text) items (word-level when available)."""
    out: list[tuple] = []
    for line in getattr(result, "text_lines", []) or []:
        words = getattr(line, "words", None)
        if words:
            for w in words:
                bb, t = w.bbox, (w.text or "").strip()
                if t:
                    out.append((bb[0], bb[2], bb[1], t))
        else:
            bb, t = line.bbox, (line.text or "").strip()
            if t:
                out.append((bb[0], bb[2], bb[1], t))
    return out


def parse_pdf_surya(path: str | Path, max_pages: int | None = None) -> ParseResult:
    rec, det = _predictors()
    doc = fitz.open(str(path))
    n_total = len(doc)
    limit = min(n_total, max_pages or GROQ_EXTRACT_MAX_PAGES)
    images = [_render(doc[i], SURYA_DPI) for i in range(limit)]
    widths = [img.width for img in images]
    doc.close()

    results = rec(
        images,
        det_predictor=det,
        recognition_batch_size=SURYA_REC_BATCH,
        detection_batch_size=SURYA_DET_BATCH,
        return_words=True,
        sort_lines=True,
    )

    result = ParseResult(file_format="pdf")
    raw: list[str] = []
    for res, width in zip(results, widths):
        items = _items(res)
        raw.append("\n".join(t for *_, t in items))
        rows, labels = interpret_grid(grid_from_items(items, width))
        result.rows.extend(rows)
        if labels and not result.price_labels:
            result.price_labels = labels

    result.raw_text = "\n".join(raw)
    result.warnings.append(
        f"surya ocr ({SURYA_DEVICE}): {len(result.rows)} rows from {limit}/{n_total} pages"
    )
    if not result.rows:
        result.warnings.append("surya produced no rows")
    return result
