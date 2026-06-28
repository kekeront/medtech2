"""Structured price-row extraction service: Groq (Qwen3-VL) primary, local fallback.

A single entry point — `extract(path)` — that returns normalized price rows regardless of
which backend produced them. PDFs go to the Groq LLM extractor first (best quality on the
messy, OCR-degraded layouts); if Groq is unavailable, rate-limited, errors, or returns
nothing, it transparently falls back to the local parser stack (fitz geometry / EasyOCR).
Non-PDF formats (xlsx/docx) use the local grid parser directly — it already scores 95-100%.

Exposed over HTTP by app/extract_api.py (`POST /extract`, `GET /extract/health`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .parsers import ParseResult, parse_file
from .parsers.detect import detect_format
from .tariffs import map_tariffs

logger = logging.getLogger(__name__)

_PDF = ("pdf", "scan_pdf")


@dataclass
class ExtractionResult:
    backend: str  # "groq" | "local"
    file_format: str
    rows: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fallback_reason: str | None = None  # why Groq was skipped/abandoned, if it was

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    def as_dict(self) -> dict:
        return {
            "backend": self.backend,
            "file_format": self.file_format,
            "n_rows": self.n_rows,
            "fallback_reason": self.fallback_reason,
            "warnings": self.warnings,
            "rows": self.rows,
        }


def _serialize(pr: ParseResult) -> list[dict]:
    """Normalize a ParseResult's rows into the shared price-row dict shape, resolving
    resident / nonresident / extra tariffs (pre-resolved by the LLM, else via map_tariffs)."""
    out: list[dict] = []
    for row in pr.rows:
        if row.tariffs_resolved:
            res, non, extra = row.resident, row.nonresident, row.extra_tiers or {}
        else:
            res, non, extra = map_tariffs(row.prices, pr.price_labels)
        out.append(
            {
                "name": row.name,
                "code": row.code,
                "unit": row.unit,
                "section": row.section,
                "price_resident": res,
                "price_nonresident": non,
                "price_extra_tiers": extra or None,
                "currency": row.currency,
            }
        )
    return out


def _groq_extract(path: Path, fmt: str, vision: bool) -> ParseResult:
    from .parsers.pdf_groq import parse_pdf_groq

    return parse_pdf_groq(path, vision=vision)


def _local_extract(path: Path, fmt: str) -> ParseResult:
    # The geometric/grid/OCR stack; force OCR for image scans.
    return parse_file(path, file_format=fmt, ocr=(fmt == "scan_pdf"))


def extract(
    path: str | Path,
    file_format: str | None = None,
    backend: str = "auto",
    vision: bool = False,
    min_rows: int = 1,
) -> ExtractionResult:
    """Extract structured rows. backend: "auto" (Groq→local), "groq", or "local"."""
    path = Path(path)
    fmt = file_format or detect_format(path)

    # Non-PDF: the local grid parser is already best-in-class; skip the LLM.
    if fmt not in _PDF:
        pr = _local_extract(path, fmt)
        return ExtractionResult("local", fmt, _serialize(pr), pr.warnings)

    fallback_reason: str | None = None
    if backend in ("auto", "groq"):
        from .ocr_correction import groq_available

        if not groq_available():
            fallback_reason = "groq unavailable (no GROQ_API_KEY / package)"
        else:
            try:
                pr = _groq_extract(path, fmt, vision=vision)
                if pr.n_rows >= min_rows:
                    return ExtractionResult("groq", fmt, _serialize(pr), pr.warnings)
                fallback_reason = "groq returned no rows"
            except Exception as exc:  # noqa: BLE001 — any Groq failure falls back to local
                logger.warning("Groq extraction failed, falling back to local: %s", exc)
                fallback_reason = f"groq error: {type(exc).__name__}: {exc}"

        if backend == "groq":  # caller demanded Groq only — surface the failure
            return ExtractionResult("groq", fmt, [], [], fallback_reason)

    pr = _local_extract(path, fmt)
    return ExtractionResult("local", fmt, _serialize(pr), pr.warnings, fallback_reason)


def health() -> dict:
    from .config import GROQ_EXTRACT_MODEL, GROQ_VISION_MODEL
    from .ocr import ocr_engine_available
    from .ocr_correction import groq_available

    ocr_ok, ocr_engine = ocr_engine_available()
    return {
        "primary": "groq",
        "groq": {
            "available": groq_available(),
            "text_model": GROQ_EXTRACT_MODEL,
            "vision_model": GROQ_VISION_MODEL,
        },
        "local": {"available": True, "ocr_engine": ocr_engine if ocr_ok else None},
    }
