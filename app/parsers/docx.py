"""DOCX parser: accepts tracked changes, then interprets every table as a price grid."""

from __future__ import annotations

from pathlib import Path

import docx
from docx.oxml.ns import qn

from .base import ParseResult
from .table import interpret_grid


def _accept_tracked_changes(document) -> None:
    """Accept all tracked changes (TZ 4.2): keep inserted text, drop deleted text.

    python-docx reads only direct <w:r> children of a paragraph, so inserted runs
    wrapped in <w:ins> must be unwrapped to become visible; deletions are removed.
    """
    body = document.element.body
    # Drop deletions entirely (removes nested <w:delText> too).
    for el in list(body.iter(qn("w:del"))):
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)
    # Unwrap insertions: lift each child run out to replace the <w:ins> wrapper.
    for ins in list(body.iter(qn("w:ins"))):
        parent = ins.getparent()
        if parent is None:
            continue
        for child in list(ins):
            ins.addprevious(child)
        parent.remove(ins)


def parse_docx(path: str | Path) -> ParseResult:
    document = docx.Document(str(path))
    _accept_tracked_changes(document)

    result = ParseResult(file_format="docx")
    raw_chunks: list[str] = []

    for table in document.tables:
        grid: list[list[str]] = []
        for row in table.rows:
            cells = [c.text for c in row.cells]
            grid.append(cells)
            raw_chunks.append("\t".join(_clean(c.text) for c in row.cells))
        rows, labels = interpret_grid(grid)
        result.rows.extend(rows)
        if labels and not result.price_labels:
            result.price_labels = labels

    result.raw_text = "\n".join(raw_chunks)
    if not result.rows:
        result.warnings.append("no tabular price data found in DOCX")
    return result


def _clean(s: str) -> str:
    return " ".join((s or "").split())
