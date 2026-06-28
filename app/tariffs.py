"""Map a row's ordered price columns to resident / nonresident / extra tariffs and
convert non-KZT prices to KZT using live National Bank of Kazakhstan rates."""

from __future__ import annotations

from datetime import date

from .config import FX_TO_KZT

_NON_KW = ("нерезидент", "дальн", "не прожива", "не проживающ")
_RES_KW = ("резидент", "граждан республики", "постоянно прожива", "кандас", "оралман")


def map_tariffs(
    prices: list[float], labels: list[str]
) -> tuple[float | None, float | None, dict[str, float]]:
    """Return (resident, nonresident, extra_tiers).

    resident    — the local/граждане-РК tier (label-matched, else the first column).
    nonresident — the nearest foreign tier *to the right of* resident (label-matched or
                  positional), so a 3+-tier list keeps СНГ/ближнее here, not дальнее.
    extra_tiers — {label: price} for every remaining priced column (дальнее зарубежье,
                  страховая, партнёрская скидка, …) so no tier is dropped.

    Falls back to position (first = resident, next = nonresident) when column headers
    are missing or garbled (scanned PDFs).
    """
    if not prices:
        return None, None, {}

    have_labels = bool(labels) and len(labels) == len(prices)

    # Resident column: first header matching a resident keyword, else column 0.
    resident_idx = 0
    if have_labels:
        for i, label in enumerate(prices):  # noqa: B007 — index drives the lookup
            lc = (labels[i] or "").lower()
            if any(k in lc for k in _RES_KW):
                resident_idx = i
                break

    right = [i for i in range(resident_idx + 1, len(prices))]
    left = [i for i in range(resident_idx)]
    # Nonresident = nearest foreign column after resident; else the one before it.
    if right:
        non_idx, rest = right[0], left + right[1:]
    elif left:
        non_idx, rest = left[-1], left[:-1]
    else:
        non_idx, rest = None, []

    resident = prices[resident_idx]
    nonresident = prices[non_idx] if non_idx is not None else None
    extra: dict[str, float] = {}
    for i in rest:
        label = labels[i].strip() if have_labels and labels[i] else f"tier_{i + 1}"
        extra[label] = prices[i]
    return resident, nonresident, extra


# --------------------------------------------------------------------------- FX → KZT


# KZT-per-1-unit rates. Resolved live from the NBK feed (cached, with a static fallback
# to config.FX_TO_KZT) — see app/fx.py. Looked up per the document's effective date.
def current_rates(on_date: date | None = None) -> dict[str, float]:
    """{KZT, USD, RUB} → KZT-per-unit, from the NBK feed for `on_date` (cached, safe)."""
    from . import fx  # lazy: avoids importing httpx/defusedxml when FX isn't needed

    d = on_date.strftime("%d.%m.%Y") if on_date else None
    payload = fx.get_fx_rates(date=d)
    return {"KZT": 1.0, "USD": payload.usd_kzt, "RUB": payload.rub_kzt}


def to_kzt(
    value: float | None, currency: str, rates: dict[str, float] | None = None
) -> float | None:
    """Convert a price to KZT. `rates` (from current_rates) is reused across a document;
    when omitted, KZT passes through and non-KZT falls back to the static config table."""
    if value is None:
        return None
    if currency == "KZT":
        return round(value, 2)
    if rates is None:
        rates = {"KZT": 1.0, **{k: v for k, v in FX_TO_KZT.items()}}
    return round(value * rates.get(currency, 1.0), 2)
