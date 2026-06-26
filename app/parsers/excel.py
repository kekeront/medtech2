"""Excel parser (.xlsx / .xls): walks every sheet and interprets each as a price grid."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .base import ParseResult
from .table import interpret_grid


def parse_excel(path: str | Path, file_format: str = "xlsx") -> ParseResult:
    engine = "xlrd" if file_format == "xls" else "openpyxl"
    xls = pd.ExcelFile(path, engine=engine)

    result = ParseResult(file_format=file_format)
    raw_chunks: list[str] = []

    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet, header=None, dtype=str)
        grid = [
            ["" if pd.isna(v) else str(v) for v in row]
            for row in df.itertuples(index=False, name=None)
        ]
        raw_chunks.append(f"### sheet: {sheet}")
        raw_chunks.extend("\t".join(c for c in r if c) for r in grid[:200])

        rows, labels = interpret_grid(grid)
        result.rows.extend(rows)
        if labels and not result.price_labels:
            result.price_labels = labels

    result.raw_text = "\n".join(raw_chunks)
    if not result.rows:
        result.warnings.append("no tabular price data found in workbook")
    return result
