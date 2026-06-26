"""Dispatch a file to the right parser based on detected format."""

from __future__ import annotations

from pathlib import Path

from .base import ParseResult
from .detect import detect_format


def parse_file(path: str | Path, file_format: str | None = None) -> ParseResult:
    fmt = file_format or detect_format(path)
    if fmt in ("pdf", "scan_pdf"):
        from .pdf import parse_pdf

        return parse_pdf(path, file_format=fmt)
    if fmt == "docx":
        from .docx import parse_docx

        return parse_docx(path)
    if fmt in ("xlsx", "xls"):
        from .excel import parse_excel

        return parse_excel(path, file_format=fmt)
    raise ValueError(f"No parser for format: {fmt}")
