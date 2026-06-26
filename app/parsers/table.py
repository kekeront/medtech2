"""Generic grid interpreter shared by the DOCX and Excel parsers.

Both formats yield a 2-D grid of cell strings. The header row is rarely the first
row, columns vary per clinic, and section titles appear as merged single-cell rows.
``interpret_grid`` locates the header, classifies columns by keyword + numeric
density, and emits PriceRow objects with the running section title.
"""

from __future__ import annotations

import re

from .base import PriceRow
from .numbers import parse_price

NAME_KW = (
    "наимен",
    "услуг",
    "обследован",
    "название",
    "перечень",
    "вид иссл",
    "манипул",
    "процедур",
)
CODE_KW = ("код", "шифр", "тарификат")
UNIT_KW = ("ед.изм", "ед. изм", "единиц", "измерения", "кол-во", "кол.во")
PRICE_KW = (
    "цена",
    "цены",
    "стоим",
    "тариф",
    "прайс",
    "ндс",
    "тенге",
    "руб",
    "тг",
    "kzt",
)
ORDINAL_KW = ("№", "п/п", "n п", "no")

_NUM_ONLY = re.compile(r"^[\d.,\s ]+$")


def _norm(s) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def _is_number_cell(s: str) -> bool:
    """True only for cells that are purely numeric (no letters) — excludes codes like 'U1.1'."""
    s = re.sub(r"(тенге|тнг|тг|₸|kzt|руб|rub)\.?", "", s, flags=re.IGNORECASE).strip()
    if not s or not _NUM_ONLY.match(s):
        return False
    return bool(re.search(r"\d", s))


def _has(header: str, kws) -> bool:
    h = header.lower()
    return any(k in h for k in kws)


def _find_header(grid: list[list[str]], scan: int = 60) -> int | None:
    """Index of the row that names a service column and at least one price column."""
    best = None
    for i, row in enumerate(grid[:scan]):
        joined = " | ".join(c.lower() for c in row)
        if _has(joined, NAME_KW) and _has(joined, PRICE_KW):
            return i
        # fallback candidate: has a name column even without an obvious price header
        if best is None and _has(joined, NAME_KW) and sum(bool(c) for c in row) >= 2:
            best = i
    return best


def _classify_columns(grid, header_idx, ncols):
    header = [
        grid[header_idx][c] if c < len(grid[header_idx]) else "" for c in range(ncols)
    ]
    data = grid[header_idx + 1 :]

    name_col = code_col = unit_col = ordinal_col = None
    price_cols: list[int] = []
    for c in range(ncols):
        h = header[c].lower()
        # Code is checked before name because "Код услуги" contains the name keyword "услуг".
        if code_col is None and _has(h, CODE_KW):
            code_col = c
        elif ordinal_col is None and _has(h, ORDINAL_KW):
            ordinal_col = c
        elif name_col is None and _has(h, NAME_KW):
            name_col = c
        elif unit_col is None and _has(h, UNIT_KW):
            unit_col = c
        elif _has(h, PRICE_KW) and not _has(h, UNIT_KW):
            price_cols.append(c)

    skip = {name_col, code_col, unit_col, ordinal_col}

    # Numeric-density fallback: a column whose data cells are mostly clean numbers is a price.
    def density(c):
        vals = [_norm(r[c]) for r in data if c < len(r)]
        vals = [v for v in vals if v]
        if not vals:
            return 0.0
        return sum(_is_number_cell(v) for v in vals) / len(vals)

    if not price_cols:
        price_cols = [c for c in range(ncols) if c not in skip and density(c) >= 0.5]

    # If the service column wasn't named, pick the column with the longest text that
    # isn't a price / code / unit / ordinal column.
    if name_col is None:
        candidates = [c for c in range(ncols) if c not in price_cols and c not in skip]
        if candidates:
            name_col = max(
                candidates,
                key=lambda c: sum(len(_norm(r[c])) for r in data if c < len(r)),
            )

    price_cols = [c for c in price_cols if c not in {name_col, code_col, ordinal_col}]
    return name_col, code_col, unit_col, price_cols, header


def _is_section_row(cells, name_col, price_cols) -> bool:
    """A row with text only in the name area and no prices is a section title."""
    nonempty = [c for c in cells if c]
    if not nonempty:
        return False
    has_price = any(c < len(cells) and _is_number_cell(cells[c]) for c in price_cols)
    if has_price:
        return False
    distinct = set(nonempty)
    # merged section cell repeated across columns, or a single text cell
    return len(distinct) == 1 or len(nonempty) == 1


def _is_numbering_row(cells) -> bool:
    """Skip a '1 | 2 | 3 | 4' column-enumeration row."""
    vals = [c for c in cells if c]
    return len(vals) >= 2 and all(re.fullmatch(r"\d{1,2}", v) for v in vals)


def interpret_grid(grid: list[list[str]]) -> tuple[list[PriceRow], list[str]]:
    grid = [[_norm(c) for c in row] for row in grid]
    grid = [row for row in grid if any(row)]
    if not grid:
        return [], []
    ncols = max(len(r) for r in grid)
    grid = [r + [""] * (ncols - len(r)) for r in grid]

    header_idx = _find_header(grid)
    if header_idx is None:
        return [], []
    name_col, code_col, unit_col, price_cols, header = _classify_columns(
        grid, header_idx, ncols
    )
    if name_col is None:
        return [], []

    price_labels = [header[c] for c in price_cols]
    rows: list[PriceRow] = []
    section = None
    for cells in grid[header_idx + 1 :]:
        if _is_numbering_row(cells):
            continue
        if _is_section_row(cells, name_col, price_cols):
            section = cells[name_col] or next((c for c in cells if c), None)
            continue
        name = cells[name_col] if name_col < len(cells) else ""
        prices = []
        for c in price_cols:
            v, _ = parse_price(cells[c]) if c < len(cells) else (None, True)
            if v is not None:
                prices.append(v)
        if not name and not prices:
            continue
        if not name:
            # price without a service name — keep for review rather than dropping silently
            name = "(?)"
        rows.append(
            PriceRow(
                name=name,
                code=(
                    cells[code_col]
                    if code_col is not None and code_col < len(cells)
                    else None
                )
                or None,
                unit=(
                    cells[unit_col]
                    if unit_col is not None and unit_col < len(cells)
                    else None
                )
                or None,
                section=section,
                prices=prices,
                raw=" | ".join(c for c in cells if c),
            )
        )
    return rows, price_labels
