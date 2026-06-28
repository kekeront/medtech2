"""FastAPI router for live NBK exchange rates (GET /fx/rates, GET /fx/health).

Mirrors the ocr_api.py router pattern:
  router = APIRouter(prefix="/fx", tags=["fx"])

Wired into the main app via app.include_router(fx_router) in api.py.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Query

from . import config
from . import fx as fx_client
from .schemas import FxRate, FxRatesOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fx", tags=["fx"])

# DD.MM.YYYY — the format the NBK API expects.
_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")


def _validate_date(date_str: str) -> None:
    """Raise HTTPException(422) if date_str is not a valid DD.MM.YYYY date.

    Validates both the format (regex) and the calendar value (strptime) so that
    malformed strings are never forwarded to the NBK API (which returns a 500 on them).
    """
    if not _DATE_RE.match(date_str):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid date format: {date_str!r}. "
                "Expected DD.MM.YYYY (e.g. 27.06.2026). "
                "Note: YYYY-MM-DD is not accepted."
            ),
        )
    try:
        datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid date value: {date_str!r}: {exc}",
        ) from exc


@router.get("/rates", response_model=FxRatesOut)
def get_rates(
    date: str | None = Query(
        None,
        description=(
            "Historical date in DD.MM.YYYY format (e.g. 27.06.2026). "
            "Omit to get the latest available rates."
        ),
    ),
    refresh: bool = Query(
        False,
        description="Force bypass of the in-memory cache and re-fetch from NBK.",
    ),
) -> FxRatesOut:
    """Return live USD->KZT and RUB->KZT exchange rates from the National Bank of Kazakhstan.

    Rate direction: ``per_unit_kzt`` is how many KZT you receive for **1 unit** of
    the foreign currency.  Example: USD per_unit_kzt=486.47 means 1 USD = 486.47 KZT.

    - ``source="nbk"``      — live feed was used successfully.
    - ``source="fallback"`` — NBK was unreachable; env-overridable defaults from config
      (FX_USD_KZT / FX_RUB_KZT) are returned.  The endpoint never returns 5xx just
      because NBK is down.
    - ``stale=true``        — the fallback config values are being served.
    - ``as_of``             — date string (DD.MM.YYYY) from the NBK feed; null on fallback.
    - ``error``             — human-readable reason when source is "fallback".
    """
    if date is not None:
        _validate_date(date)

    payload = fx_client.get_fx_rates(date=date, refresh=refresh)

    def _build_rate(entry: fx_client.RateEntry | None, fallback_val: float) -> FxRate:
        if entry is not None:
            return FxRate(
                per_unit_kzt=round(entry.per_unit_kzt, 4),
                quant=entry.quant,
                raw=entry.raw,
            )
        # Fallback: no NBK entry, use the config value directly.
        return FxRate(per_unit_kzt=round(fallback_val, 4), quant=1, raw=fallback_val)

    return FxRatesOut(
        base="KZT",
        as_of=payload.as_of,
        source=payload.source,  # type: ignore[arg-type]  # Literal checked at runtime
        stale=payload.stale,
        rates={
            "USD": _build_rate(payload.usd_entry, payload.usd_kzt),
            "RUB": _build_rate(payload.rub_entry, payload.rub_kzt),
        },
        error=payload.error,
    )


@router.get("/health")
def fx_health() -> dict:
    """Report NBK feed reachability and in-memory cache state.

    Performs a lightweight HEAD request to the NBK rates_all.xml feed.
    Cache state is read from the module-level in-memory cache without a network call.
    """
    nbk_reachable = False
    nbk_error: str | None = None

    try:
        with httpx.Client(
            timeout=5.0,
            follow_redirects=True,
            headers={"User-Agent": "medarchive/1.0"},
        ) as c:
            r = c.head(config.NBK_RATES_ALL_URL)
            nbk_reachable = r.status_code < 500
    except httpx.HTTPError as exc:
        nbk_error = f"{type(exc).__name__}: {exc}"

    return {
        "nbk_reachable": nbk_reachable,
        "nbk_error": nbk_error,
        "cache": fx_client.cache_info(),
    }
