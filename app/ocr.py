"""Optional OCR layer for true image-scanned price-list PDFs.

Engine priority:
  1. EasyOCR with langs=['ru', 'en'] — full Cyrillic support.  Models (~80 MB total)
     are downloaded automatically to ~/.EasyOCR/model/ on first use.
  2. rapidocr-onnxruntime — fast fallback, but its bundled dictionary is Latin/Chinese
     only; Cyrillic will not be produced.  Used only when EasyOCR is unavailable.

Both engines are lazy-loaded: importing this module (and therefore app.api) is instant.
The engine is instantiated on the first OCR call and cached for subsequent calls.

PDFs are rendered to RGB images via PyMuPDF at the requested DPI, then passed
page-by-page to the active engine.

Structured mode converts OCR bounding boxes into a row/column layout using the same
geometry heuristics as app/parsers/pdf.py and returns PriceRow-compatible dicts.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF, already a project dependency
import numpy as np

from .parsers.numbers import price_cell_value

# --------------------------------------------------------------------------- engine state
# Populated on the first call to _get_engine(); never at import time.

_ENGINE: Any = None
_ENGINE_NAME: str = ""
_ENGINE_ERROR: str = ""


def _load_engine() -> tuple[Any, str, str]:
    """Try EasyOCR (Cyrillic-capable) then fall back to rapidocr.

    Returns (engine, name, error_message).  engine is None only if every
    backend fails; error_message is non-empty in that case.
    """
    # ---- EasyOCR (preferred: genuine Cyrillic output) -----------------------
    try:
        import easyocr  # noqa: PLC0415 — intentionally lazy

        reader = easyocr.Reader(["ru", "en"], gpu=False, verbose=False)
        return reader, "easyocr-ru", ""
    except Exception as easyocr_exc:  # noqa: BLE001
        easyocr_err = str(easyocr_exc)

    # ---- rapidocr-onnxruntime (fallback: no Cyrillic) -----------------------
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415

        engine = RapidOCR()
        return (
            engine,
            "rapidocr-onnxruntime",
            f"easyocr unavailable ({easyocr_err}); using rapidocr (Cyrillic not supported)",
        )
    except Exception:  # noqa: BLE001
        pass

    return None, "", f"No OCR engine available — easyocr: {easyocr_err}"


def _get_engine() -> tuple[Any, str, str]:
    """Return the cached engine, loading it on the first call."""
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


# --------------------------------------------------------------------------- engine call


def _run_engine(engine: Any, engine_name: str, img: np.ndarray) -> list[list]:
    """Run OCR and return a normalised list of [box, text, score] items.

    box is [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] (4 corners, any winding order).
    This unified format is consumed by both the plain-text path and structured mode.
    """
    if engine_name.startswith("easyocr"):
        raw = engine.readtext(img)
        # EasyOCR: [(box, text, confidence)] — box already [[x,y]×4]
        return [[list(box), text, score] for box, text, score in raw]
    else:
        # rapidocr: (results_or_None, elapsed) — results is [[box, text, score], ...]
        raw, _elapsed = engine(img)
        return raw or []


# --------------------------------------------------------------------------- page rendering


def _render_page(page: Any, dpi: int) -> np.ndarray:
    """Render a PDF page to an RGB numpy array at the given DPI."""
    zoom = dpi / 72.0  # PyMuPDF native resolution is 72 DPI
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)


# --------------------------------------------------------------------------- geometry helpers (structured mode)

# Tuned for ~300 DPI pixel coordinates.
_Y_TOL_PX = 12.0  # cluster words whose y0 differ by less than this into one row
_GAP_FACTOR = 1.6  # a gap > factor × median intra-word gap signals a cell break
_GAP_FLOOR_PX = 18.0  # minimum gap to consider a cell break (~7 pt at 72 DPI)


def _box_coords(box: list[list[float]]) -> tuple[float, float, float, float]:
    """Extract (x0, y0, x1, y1) from a 4-point bounding box (any winding order)."""
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def _cluster_into_rows(
    items: list[tuple[str, float, float, float, float]],
) -> list[list[tuple[str, float, float, float, float]]]:
    """Cluster (text, x0, y0, x1, y1) items into visual rows by y proximity."""
    if not items:
        return []
    items = sorted(items, key=lambda t: (t[2], t[1]))
    rows: list[list[tuple[str, float, float, float, float]]] = []
    cur: list[tuple[str, float, float, float, float]] = []
    cur_y: float | None = None
    for item in items:
        y0 = item[2]
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
    row = sorted(row, key=lambda t: t[1])
    if not row:
        return []

    gaps = [row[i][1] - row[i - 1][3] for i in range(1, len(row))]
    positive = [g for g in gaps if g > 0]
    median = sorted(positive)[len(positive) // 2] if positive else 0.0
    threshold = max(_GAP_FLOOR_PX, median * _GAP_FACTOR)

    cells: list[tuple[str, float, float]] = []
    chunk: list[tuple[str, float, float, float, float]] = [row[0]]
    for prev, cur in zip(row, row[1:]):
        if cur[1] - prev[3] > threshold:
            cells.append(
                (" ".join(t[0] for t in chunk).strip(), chunk[0][1], chunk[-1][3])
            )
            chunk = [cur]
        else:
            chunk.append(cur)
    cells.append((" ".join(t[0] for t in chunk).strip(), chunk[0][1], chunk[-1][3]))
    return cells


def _ocr_results_to_structured(ocr_items: list[list]) -> list[dict[str, Any]]:
    """Convert one page of OCR output into PriceRow-compatible dicts (best-effort).

    Uses the same geometry strategy as app/parsers/pdf.py: cluster by y → segment
    cells by x-gap → trailing numeric cells are prices, leading text is service name.
    """
    import re  # noqa: PLC0415

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
        return bool(
            len(letters) >= 4
            and sum(c.isupper() for c in letters) / len(letters) >= 0.75
        )

    word_items: list[tuple[str, float, float, float, float]] = []
    for item in ocr_items:
        if len(item) < 2:
            continue
        box, text = item[0], item[1]
        if not text or not text.strip():
            continue
        x0, y0, x1, y1 = _box_coords(box)
        word_items.append((text.strip(), x0, y0, x1, y1))

    section: str | None = None
    pending_name: list[str] = []
    result_rows: list[dict[str, Any]] = []

    for row in _cluster_into_rows(word_items):
        cells = _segment_cells(row)
        if not cells:
            continue

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
            {"name": name or "(?)", "code": code, "prices": prices, "section": section}
        )

    return result_rows


# --------------------------------------------------------------------------- public API


def ocr_pdf(
    path: str | Path,
    dpi: int = 300,
    max_pages: int | None = None,
    structured: bool = False,
    max_seconds: float | None = None,
) -> dict[str, Any]:
    """Run OCR on every page of a PDF.

    Args:
        path: Path to the PDF file.
        dpi: Render resolution in dots per inch.  Higher = better accuracy, slower.
        max_pages: Stop after this many pages (None = all pages).
        structured: When True, also attempt row/column reconstruction into
            PriceRow-like dicts (best-effort; accuracy depends on scan quality).

    Returns a dict with keys: engine, pages, full_text, char_count, elapsed_sec,
    and optionally structured_rows when structured=True.
    """
    engine, engine_name, err = _get_engine()
    if engine is None:
        raise RuntimeError(err or "OCR engine not available")

    path = Path(path)
    t_start = time.perf_counter()
    doc = fitz.open(str(path))
    total_pages = len(doc)
    limit = min(total_pages, max_pages) if max_pages else total_pages

    pages_out: list[dict[str, Any]] = []
    text_parts: list[str] = []
    all_structured: list[dict[str, Any]] = []
    truncated = False

    for page_no in range(limit):
        # Wall-clock budget (TZ 5: ≤3 min/doc) — stop and return partial results.
        if (
            max_seconds
            and page_no > 0
            and (time.perf_counter() - t_start) > max_seconds
        ):
            truncated = True
            break
        img = _render_page(doc[page_no], dpi)
        items = _run_engine(engine, engine_name, img)

        page_words = [item[1] for item in items if item and len(item) >= 2]
        if structured and items:
            all_structured.extend(_ocr_results_to_structured(items))

        page_text = "\n".join(page_words)
        text_parts.append(page_text)
        pages_out.append(
            {"page": page_no + 1, "text": page_text, "n_words": len(page_words)}
        )

    doc.close()
    full_text = "\n\n".join(text_parts)
    out: dict[str, Any] = {
        "engine": engine_name,
        "pages": pages_out,
        "pages_total": total_pages,
        "truncated": truncated,
        "full_text": full_text,
        "char_count": len(full_text),
        "elapsed_sec": round(time.perf_counter() - t_start, 3),
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

    from PIL import Image  # noqa: PLC0415 — Pillow is always present (rapidocr dep)

    t_start = time.perf_counter()
    img = np.array(Image.open(str(path)).convert("RGB"))
    items = _run_engine(engine, engine_name, img)

    words = [item[1] for item in items if item and len(item) >= 2]
    structured_rows = _ocr_results_to_structured(items) if structured and items else []
    text = "\n".join(words)

    out: dict[str, Any] = {
        "engine": engine_name,
        "pages": [{"page": 1, "text": text, "n_words": len(words)}],
        "full_text": text,
        "char_count": len(text),
        "elapsed_sec": round(time.perf_counter() - t_start, 3),
    }
    if structured:
        out["structured_rows"] = structured_rows
    return out
