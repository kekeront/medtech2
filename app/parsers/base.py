"""Shared intermediate representation produced by every parser."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PriceRow:
    """One extracted price-list line, before DB normalization."""

    name: str
    code: str | None = None
    unit: str | None = None
    section: str | None = None
    # Prices in left-to-right column order, already cleaned to floats (KZT unless `currency` set).
    prices: list[float] = field(default_factory=list)
    currency: str = "KZT"
    raw: str = ""
    # Per-row parse warnings (e.g. "price repaired from OCR", "no price found").
    issues: list[str] = field(default_factory=list)
    # Pre-resolved tariffs: set by parsers that already understand the columns (e.g. the
    # LLM extractor), so the pipeline skips positional map_tariffs guessing. resident /
    # nonresident in `currency`; extra_tiers maps any further tier label -> value.
    tariffs_resolved: bool = False
    resident: float | None = None
    nonresident: float | None = None
    extra_tiers: dict[str, float] | None = None


@dataclass
class ParseResult:
    file_format: str  # pdf / docx / xlsx / xls / scan_pdf
    rows: list[PriceRow] = field(default_factory=list)
    raw_text: str = ""  # full extracted text, stored for audit
    # Header labels for each price column, in order — used to map columns to resident/nonresident.
    price_labels: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def n_rows(self) -> int:
        return len(self.rows)
