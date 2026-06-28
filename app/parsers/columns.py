"""Projection-profile column model.

Given positioned text items — (x0, x1, top, text) — find column separators (empty
vertical bands in the x-coverage profile, a standard layout-analysis method) and emit
aligned grid rows that table.interpret_grid can classify. Shared by any parser that has
word/line positions (e.g. the Surya OCR path).
"""

from __future__ import annotations

_ROW_TOL = 6.0  # items whose `top` differ by less than this share a visual row
_MIN_GAP = 6  # an empty x-band at least this wide (px) is a column separator


def column_bounds(
    items: list[tuple], page_width: float, min_gap: int = _MIN_GAP
) -> list[float]:
    """Column separators from the x projection profile: midpoints of interior empty bands."""
    width = int(page_width) + 2
    cover = [0] * width
    for x0, x1, *_ in items:
        a, b = max(0, int(x0)), min(width - 1, int(x1))
        for x in range(a, b + 1):
            cover[x] += 1

    seps: list[float] = []
    x = 0
    while x < width:
        if cover[x] == 0:
            start = x
            while x < width and cover[x] == 0:
                x += 1
            if start > 0 and x < width - 1 and (x - start) >= min_gap:
                seps.append((start + x) / 2.0)
        else:
            x += 1
    return [0.0, *seps, float(page_width)]


def _assign(items: list[tuple], bounds: list[float]) -> list[str]:
    cells = [""] * (len(bounds) - 1)
    # Reading order within a cell (top, then left) so a name wrapped across two visual
    # lines stays in order instead of interleaving by x.
    for x0, x1, _top, text in sorted(items, key=lambda w: (round(w[2]), w[0])):
        center = (x0 + x1) / 2.0
        col = 0
        for i in range(len(bounds) - 1):
            if bounds[i] <= center < bounds[i + 1]:
                col = i
                break
        cells[col] = (cells[col] + " " + text).strip() if cells[col] else text
    return cells


def grid_from_items(
    items: list[tuple], page_width: float, row_tol: float = _ROW_TOL
) -> list[list[str]]:
    """Cluster items into visual rows by `top`, then split each row into column cells."""
    if not items:
        return []
    bounds = column_bounds(items, page_width)
    rows: list[list[str]] = []
    cur: list[tuple] = []
    cur_top: float | None = None
    for it in sorted(items, key=lambda w: (round(w[2], 1), w[0])):
        if cur_top is None or abs(it[2] - cur_top) <= row_tol:
            cur.append(it)
            cur_top = it[2] if cur_top is None else (cur_top + it[2]) / 2
        else:
            rows.append(_assign(cur, bounds))
            cur, cur_top = [it], it[2]
    if cur:
        rows.append(_assign(cur, bounds))
    return rows
