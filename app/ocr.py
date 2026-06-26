"""Optional OCR layer for true image-scanned price-list PDFs.

Uses rapidocr-onnxruntime (pure-Python, no system deps, Cyrillic-capable).
The engine is loaded once at module level; subsequent calls reuse the cached instance.

PDFs are rendered to RGB images via PyMuPDF (already a project dependency) at the
requested DPI, then passed page-by-page to the OCR engine.

Structured mode converts OCR word bounding boxes into a row/column layout using the
same geometry heuristics as app/parsers/pdf.py and returns PriceRow-compatible dicts.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF, already installed
import numpy as np

from .parsers.numbers import price_cell_value

# --------------------------------------------------------------------------- engine init

_ENGINE: Any = None
_ENGINE_NAME: str = ""
_ENGINE_ERROR: str = ""


def _load_engine() -> tuple[Any, str, str]:
    """Load the OCR engine once; returns (engine, name, error_msg)."""
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import]

        engine = RapidOCR()
        return engine, "rapidocr-onnxruntime", ""
    except Exception as exc:  # noqa: BLE001
        return None, "", f"rapidocr-onnxruntime unavailable: {exc}"


def _get_engine() -> tuple[Any, str, str]:
    global _ENGINE, _ENGINE_NAME, _ENGINE_ERROR
    if _ENGINE is None and not _ENGINE_ERROR:
        _ENGINE, _ENGINE_NAME, _ENGINE_ERROR = _load_engine()
    return _ENGINE, _ENGINE_NAME, _ENGINE_ERROR


def ocr_engine_available() -> tuple[bool, str]:
    """Return (available, description) for the active OCR engine."""
    engine, name, err = _get_engine()
    if engine is not None:
        return True, name
    return False, err


# --------------------------------------------------------------------------- page rendering


def _render_page(page, dpi: int) -> np.ndarray:
    """Render a PDF page to an RGB numpy array at the given DPI."""
    zoom = dpi / 72.0  # PyMuPDF base resolution is 72 DPI
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)


# --------------------------------------------------------------------------- geometry helpers (structured mode)

# Mirror the tuning from app/parsers/pdf.py, adapted for pixel coords at 300 DPI.
_Y_TOL_PX = 12.0  # pixels; ~5 pt at 72 DPI × 300/72 ≈ 20 px, use tighter value
_GAP_FACTOR = 1.6
_GAP_FLOOR_PX = 18.0  # ~7 pt equivalent


def _box_coords(box: list[list[float]]) -> tuple[float, float, float, float]:
    """Extract (x0, y0, x1, y1) from a 4-point OCR bounding box."""
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def _cluster_into_rows(
    items: list[tuple[str, float, float, float, float]],
) -> list[list[tuple[str, float, float, float, float]]]:
    """Cluster (text, x0, y0, x1, y1) items into visual rows by y proximity."""
    if not items:
        return []
    items = sorted(items, key=lambda t: (t[2], t[1]))  # sort by y0, then x0
    rows: list[list[tuple[str, float, float, float, float]]] = []
    cur: list[tuple[str, float, float, float, float]] = []
    cur_y: float | None = None
    for item in items:
        _, _, y0, _, _ = item
        if cur_y is None or abs(y0 - cur_y) <= _Y_TOL_PX:
            cur.append(item)
            cur_y = y0 if cur_y is None else (cur_y + y0) / 2
        else:
            rows.append(cur)
            cur = [item]
            cur_y = y0
    if cur:
        rows.append(cur)
    return rows


def _segment_cells(
    row: list[tuple[str, float, float, float, float]],
) -> list[tuple[str, float, float]]:
    """Split a row of (text, x0, y0, x1, y1) items into (text, x0, x1) cells."""
    row = sorted(row, key=lambda t: t[1])  # by x0
    if not row:
        return []

    gaps = [row[i][1] - row[i - 1][3] for i in range(1, len(row))]
    positive = [g for g in gaps if g > 0]
    median = sorted(positive)[len(positive) // 2] if positive else 0.0
    threshold = max(_GAP_FLOOR_PX, median * _GAP_FACTOR)

    cells: list[tuple[str, float, float]] = []
    chunk: list[tuple[str, float, float, float, float]] = [row[0]]
    for prev, cur in zip(row, row[1:]):
        gap = cur[1] - prev[3]
        if gap > threshold:
            text = " ".join(t[0] for t in chunk).strip()
            cells.append((text, chunk[0][1], chunk[-1][3]))
            chunk = [cur]
        else:
            chunk.append(cur)
    text = " ".join(t[0] for t in chunk).strip()
    cells.append((text, chunk[0][1], chunk[-1][3]))
    return cells


def _ocr_results_to_structured(
    ocr_items: list[list],
) -> list[dict[str, Any]]:
    """Convert rapidocr output for one page into PriceRow-compatible dicts.

    Each item in ocr_items is [box_4pts, text, score].
    Returns list of {name, code, prices, section} dicts (best-effort).
    """
    import re

    _CODE_RE = re.compile(r"^[A-ZА-ЯЁ]{1,5}[\d]+(?:[.\-]\d+)*$", re.IGNORECASE)
    _ORDINAL_RE = re.compile(r"^\d{1,4}$")
    _SECTION_RE = re.compile(
        r"^(раздел|блок|подраздел|глава|часть|приложение|прейскурант)\b",
        re.IGNORECASE,
    )

    def _is_section(text: str) -> bool:
        if not text or len(text) > 80:
            return False
        if _SECTION_RE.match(text):
            return True
        letters = [c for c in text if c.isalpha()]
        if (
            len(letters) >= 4
            and sum(c.isupper() for c in letters) / len(letters) >= 0.75
        ):
            return True
        return False

    # Convert OCR items to (text, x0, y0, x1, y1) tuples
    word_items: list[tuple[str, float, float, float, float]] = []
    for item in ocr_items:
        if len(item) < 2:
            continue
        box, text = item[0], item[1]
        if not text or not text.strip():
            continue
        x0, y0, x1, y1 = _box_coords(box)
        word_items.append((text.strip(), x0, y0, x1, y1))

    rows = _cluster_into_rows(word_items)
    result_rows: list[dict[str, Any]] = []
    section: str | None = None
    pending_name: list[str] = []

    for row in rows:
        cells = _segment_cells(row)
        if not cells:
            continue

        # Determine trailing price run
        prices: list[float] = []
        cut = len(cells)
        for i in range(len(cells) - 1, -1, -1):
            v = price_cell_value(cells[i][0])
            if v is None:
                break
            prices.insert(0, v)
            cut = i

        left_text = " ".join(cells[i][0] for i in range(cut) if cells[i][0]).strip()

        if not prices:
            if left_text and _is_section(left_text):
                section = left_text
                pending_name.clear()
            elif left_text:
                pending_name.append(left_text)
            continue

        # Extract optional service code from left text
        code: str | None = None
        tokens = left_text.split()
        if tokens and _CODE_RE.match(tokens[0]):
            code, tokens = tokens[0], tokens[1:]
        elif tokens and _ORDINAL_RE.match(tokens[0]):
            tokens = tokens[1:]
        name = " ".join(tokens).strip()
        if pending_name:
            name = (" ".join(pending_name) + " " + name).strip()
            pending_name.clear()

        result_rows.append(
            {
                "name": name or "(?)",
                "code": code,
                "prices": prices,
                "section": section,
            }
        )

    return result_rows


# --------------------------------------------------------------------------- public API


def ocr_pdf(
    path: str | Path,
    dpi: int = 300,
    max_pages: int | None = None,
    structured: bool = False,
) -> dict[str, Any]:
    """Run OCR on every page of a PDF.

    Args:
        path: Path to the PDF file.
        dpi: Render resolution (higher = better accuracy, slower). Default 300.
        max_pages: Stop after this many pages (None = all).
        structured: If True, also attempt row/column reconstruction into
            PriceRow-like dicts (best-effort; may miss rows on complex layouts).

    Returns dict with keys: engine, pages, full_text, char_count, elapsed_sec,
    and optionally structured_rows when structured=True.
    """
    engine, engine_name, err = _get_engine()
    if engine is None:
        raise RuntimeError(err or "OCR engine not available")

    path = Path(path)
    t_start = time.perf_counter()

    doc = fitz.open(str(path))
    total = len(doc)
    limit = min(total, max_pages) if max_pages else total

    pages_out: list[dict[str, Any]] = []
    all_text_parts: list[str] = []
    all_structured: list[dict[str, Any]] = []

    for page_no in range(limit):
        page = doc[page_no]
        img = _render_page(page, dpi)

        ocr_result, _elapse = engine(img)

        page_words: list[str] = []
        if ocr_result:
            page_words = [item[1] for item in ocr_result if item and len(item) >= 2]
            if structured:
                all_structured.extend(_ocr_results_to_structured(ocr_result))

        page_text = "\n".join(page_words)
        all_text_parts.append(page_text)
        pages_out.append(
            {
                "page": page_no + 1,
                "text": page_text,
                "n_words": len(page_words),
            }
        )

    doc.close()
    full_text = "\n\n".join(all_text_parts)
    elapsed = round(time.perf_counter() - t_start, 3)

    out: dict[str, Any] = {
        "engine": engine_name,
        "pages": pages_out,
        "full_text": full_text,
        "char_count": len(full_text),
        "elapsed_sec": elapsed,
    }
    if structured:
        out["structured_rows"] = all_structured
    return out


def ocr_image(path: str | Path, structured: bool = False) -> dict[str, Any]:
    """Run OCR on a single image file (PNG / JPEG / TIFF / BMP).

    Returns the same dict shape as ocr_pdf with a single page entry.
    """
    engine, engine_name, err = _get_engine()
    if engine is None:
        raise RuntimeError(err or "OCR engine not available")

    from PIL import Image  # Pillow is a rapidocr dep, always present

    path = Path(path)
    t_start = time.perf_counter()

    img = np.array(Image.open(str(path)).convert("RGB"))
    ocr_result, _elapse = engine(img)

    words: list[str] = []
    structured_rows: list[dict[str, Any]] = []
    if ocr_result:
        words = [item[1] for item in ocr_result if item and len(item) >= 2]
        if structured:
            structured_rows = _ocr_results_to_structured(ocr_result)

    text = "\n".join(words)
    elapsed = round(time.perf_counter() - t_start, 3)

    out: dict[str, Any] = {
        "engine": engine_name,
        "pages": [{"page": 1, "text": text, "n_words": len(words)}],
        "full_text": text,
        "char_count": len(text),
        "elapsed_sec": elapsed,
    }
    if structured:
        out["structured_rows"] = structured_rows
    return out
