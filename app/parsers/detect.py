"""File-type classification and metadata extraction from filenames."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path


def detect_format(path: str | Path) -> str:
    """Return one of pdf / scan_pdf / docx / xlsx / xls based on extension and content."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".docx":
        return "docx"
    if ext == ".xlsx":
        return "xlsx"
    if ext == ".xls":
        return "xls"
    if ext == ".pdf":
        return _classify_pdf(p)
    raise ValueError(f"Unsupported file type: {ext} ({p.name})")


def _classify_pdf(p: Path) -> str:
    """A PDF with a usable embedded text layer -> 'pdf'; image-only -> 'scan_pdf'."""
    try:
        import fitz

        doc = fitz.open(p)
        pages = min(doc.page_count, 5) or 1
        chars = sum(len(doc[i].get_text("text").strip()) for i in range(pages))
        doc.close()
        return "pdf" if chars / pages >= 200 else "scan_pdf"
    except Exception:
        return "pdf"


_PARTNER_RE = re.compile(r"(клиника\s*\d+)", re.IGNORECASE)
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def partner_from_filename(path: str | Path) -> str:
    """Extract the partner name. Falls back to a cleaned filename stem."""
    stem = Path(path).stem
    m = _PARTNER_RE.search(stem)
    if m:
        num = re.search(r"\d+", m.group(1)).group()
        return f"Клиника {num}"
    cleaned = re.sub(r"[_\-]+", " ", stem)
    cleaned = _YEAR_RE.sub("", cleaned)
    cleaned = re.sub(
        r"\b(прайс|price|прейскурант|год[а]?)\b", "", cleaned, flags=re.IGNORECASE
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,-")
    return cleaned or stem


def effective_date_from_filename(path: str | Path) -> date | None:
    """Best-effort effective date from a 4-digit year in the filename (Jan 1 of that year)."""
    years = [int(m.group()) for m in _YEAR_RE.finditer(Path(path).stem)]
    years = [y for y in years if 2000 <= y <= 2100]
    if not years:
        return None
    return date(max(years), 1, 1)
