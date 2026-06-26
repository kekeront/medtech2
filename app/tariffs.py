"""Map a row's ordered price columns to resident / nonresident tariffs + currency conversion."""

from __future__ import annotations

from .config import FX_TO_KZT

_NON_KW = ("нерезидент", "дальн", "не прожива", "не проживающ")
_RES_KW = ("резидент", "граждан республики", "постоянно прожива", "кандас", "оралман")


def map_tariffs(
    prices: list[float], labels: list[str]
) -> tuple[float | None, float | None]:
    """Return (resident, nonresident) KZT prices.

    Uses column header labels when available (Excel/DOCX); falls back to position
    (first = resident, last = nonresident) for header-garbled scanned PDFs.
    """
    if not prices:
        return None, None
    resident = nonresident = None
    if labels and len(labels) == len(prices):
        for price, label in zip(prices, labels):
            lc = (label or "").lower()
            if any(k in lc for k in _NON_KW):
                nonresident = price  # last matching column wins
            elif any(k in lc for k in _RES_KW) and resident is None:
                resident = price
    if resident is None:
        resident = prices[0]
    if nonresident is None and len(prices) > 1:
        nonresident = prices[-1]
    return resident, nonresident


def to_kzt(value: float | None, currency: str) -> float | None:
    if value is None:
        return None
    return round(value * FX_TO_KZT.get(currency, 1.0), 2)
