"""Automatic data-quality checks (TZ 4.4). Each returns review reasons, not exceptions.

The pipeline collects reasons per item; a non-empty list marks the item needs_review
and bubbles up to the document's parse_status.
"""

from __future__ import annotations

from datetime import date

# Prices above this are almost certainly OCR digit-concatenation, not real (KZT).
# Real medical line items (incl. surgical/IVF packages) stay well below this; values
# above it are cleared to NULL and flagged so one bad OCR cell can't poison a column.
IMPLAUSIBLE_PRICE = 50_000_000

# Markers that mean a header/footnote row leaked through as a data row.
_HEADER_MARKERS = (
    "наименование",
    "цена для",
    "цена с учетом",
    "единица измер",
    "прейскурант",
)


def looks_like_header_row(name: str) -> bool:
    n = (name or "").lower()
    return any(m in n for m in _HEADER_MARKERS)


def validate_item(
    *,
    name: str,
    resident: float | None,
    nonresident: float | None,
    effective_date: date | None,
    previous_resident: float | None,
    anomaly_pct: float,
) -> list[str]:
    reasons: list[str] = []

    if resident is None or resident <= 0:
        reasons.append("price missing or not positive")
    elif resident > IMPLAUSIBLE_PRICE:
        reasons.append("implausibly large price (suspected OCR error)")

    if resident is not None and nonresident is not None and nonresident < resident:
        reasons.append("nonresident price lower than resident")

    if effective_date is not None and effective_date > date.today():
        reasons.append("effective date is in the future")

    if (
        previous_resident
        and resident
        and previous_resident > 0
        and abs(resident - previous_resident) / previous_resident > anomaly_pct
    ):
        pct = round(abs(resident - previous_resident) / previous_resident * 100)
        reasons.append(f"price changed {pct}% vs previous version")

    return reasons


def is_droppable_name(name: str | None) -> bool:
    """Empty / placeholder service name -> skip the row entirely (TZ: пропуск строки)."""
    return not name or name.strip() in ("", "(?)", "-", "—")
