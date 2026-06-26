"""Text-PDF parser for scanned price lists with an embedded (low-quality) OCR layer.

These PDFs have no ruling lines, so columns are reconstructed geometrically:
words are clustered into visual rows by y, segmented into cells by x-gaps, and the
trailing run of purely-numeric cells on each row is taken as the price columns
(resident / nonresident / ...). Leading text is the service code + name. Rows with
no price are treated as section headers or wrapped name fragments.
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz

from .base import ParseResult, PriceRow
from .numbers import detect_currency, price_cell_value

# Geometry tuning (PDF points).
Y_TOL = 5.0  # words within this vertical distance belong to the same visual row
GAP_FACTOR = (
    1.6  # a cell break is a gap > GAP_FACTOR × median intra-word gap on the row
)
GAP_FLOOR = 7.0

_CODE_RE = re.compile(r"^[A-ZА-ЯЁ]{1,5}[\d]+(?:[.\-]\d+)*$", re.IGNORECASE)
_ORDINAL_RE = re.compile(r"^\d{1,4}$")  # leading № column to drop from names
_SECTION_RE = re.compile(
    r"^(раздел|блок|подраздел|глава|часть|приложение|прейскурант)\b",
    re.IGNORECASE,
)


# Header/footnote noise that must never be treated as a section title.
_SECTION_NOISE = ("ндс", "тенге", "цена", "наимен", "единиц", "руб", "зарубежь")


def _is_section(text: str) -> bool:
    """Section header: keyword line, or an ALL-CAPS category label (e.g. 'ГЕМАТОЛОГИЯ')."""
    if not text or len(text) > 80:
        return False
    low = text.lower()
    if any(n in low for n in _SECTION_NOISE):
        return False
    if _SECTION_RE.match(text):
        return True
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 5 and sum(c.isupper() for c in letters) / len(letters) >= 0.75:
        return True
    return False


def parse_pdf(path: str | Path, file_format: str = "pdf") -> ParseResult:
    doc = fitz.open(str(path))
    result = ParseResult(file_format=file_format)
    raw_chunks: list[str] = []
    pending_name: list[str] = []  # buffered unpriced text lines (wrapped names)
    section: str | None = None

    for page in doc:
        for cells in _page_rows(page):
            raw_chunks.append("  ".join(c[0] for c in cells))
            prices, left_text = _split_row(cells)
            if not prices:
                # No price: section header or a wrapped name fragment.
                if left_text and _is_section(left_text):
                    section, pending_name[:] = left_text, []
                elif left_text:
                    pending_name.append(left_text)
                continue
            result.rows.append(
                _build_row(cells, prices, left_text, section, pending_name)
            )

    doc.close()
    result.raw_text = "\n".join(raw_chunks)
    if not result.rows:
        result.warnings.append("no priced rows recovered from PDF text layer")
    return result


def _page_rows(page) -> list[list[tuple[str, float, float]]]:
    """Return rows; each row is a list of (text, x0, x1) cells in left-to-right order."""
    words = page.get_text("words")  # (x0,y0,x1,y1,word,block,line,wordno)
    if not words:
        return []
    words.sort(key=lambda w: (w[1], w[0]))

    # Cluster words into rows by y proximity.
    rows: list[list] = []
    cur: list = []
    cur_y = None
    for w in words:
        if cur_y is None or abs(w[1] - cur_y) <= Y_TOL:
            cur.append(w)
            cur_y = w[1] if cur_y is None else (cur_y + w[1]) / 2
        else:
            rows.append(cur)
            cur = [w]
            cur_y = w[1]
    if cur:
        rows.append(cur)

    return [_segment_cells(r) for r in rows]


def _segment_cells(row_words: list) -> list[tuple[str, float, float]]:
    """Split a row's words into cells wherever the horizontal gap is unusually large."""
    row_words = sorted(row_words, key=lambda w: w[0])
    gaps = [row_words[i][0] - row_words[i - 1][2] for i in range(1, len(row_words))]
    positive = [g for g in gaps if g > 0]
    median = sorted(positive)[len(positive) // 2] if positive else 0
    threshold = max(GAP_FLOOR, median * GAP_FACTOR)

    cells: list[tuple[str, float, float]] = []
    chunk = [row_words[0]]
    for prev, w in zip(row_words, row_words[1:]):
        if w[0] - prev[2] > threshold:
            cells.append(_mk_cell(chunk))
            chunk = [w]
        else:
            chunk.append(w)
    cells.append(_mk_cell(chunk))
    return cells


def _mk_cell(words: list) -> tuple[str, float, float]:
    text = " ".join(w[4] for w in words).strip()
    return text, words[0][0], words[-1][2]


def _split_row(cells) -> tuple[list[float], str]:
    """Split a row into (trailing price values, leading text)."""
    if not cells:
        return [], ""
    prices: list[float] = []
    cut = len(cells)  # index where the trailing numeric run begins
    for i in range(len(cells) - 1, -1, -1):
        v = price_cell_value(cells[i][0])
        if v is None:
            break
        prices.insert(0, v)
        cut = i
    left_text = " ".join(cells[i][0] for i in range(cut) if cells[i][0]).strip()
    return prices, left_text


def _build_row(cells, prices, left_text, section, pending_name) -> PriceRow:
    # Pull a leading service code, or drop a leading № ordinal, from the left text.
    code = None
    tokens = left_text.split()
    if tokens and _CODE_RE.match(tokens[0]):
        code, tokens = tokens[0], tokens[1:]
    elif tokens and _ORDINAL_RE.match(tokens[0]):
        tokens = tokens[1:]  # bare row number, not part of the service name
    name = " ".join(tokens).strip()
    if pending_name:
        name = (" ".join(pending_name) + " " + name).strip()
        pending_name[:] = []
    return PriceRow(
        name=name or "(?)",
        code=code,
        section=section,
        prices=prices,
        currency=detect_currency(" ".join(c[0] for c in cells)),
        raw="  ".join(c[0] for c in cells),
    )
