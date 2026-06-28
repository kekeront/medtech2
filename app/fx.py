"""National Bank of Kazakhstan (NBK) exchange-rate client.

Fetches live USD->KZT and RUB->KZT rates from the NBK RSS feeds:
  - Latest:     https://nationalbank.kz/rss/rates_all.xml  (RSS 2.0)
  - Historical: https://nationalbank.kz/rss/get_rates.cfm?fdate=DD.MM.YYYY

Rate formula (CRITICAL): per_unit_kzt = float(description) / float(quant).
<quant> is NOT always 1 — e.g. AMD has quant=10, so 13.23/10 = 1.323 KZT/unit.

Two XML shapes handled by the same parser (iterate .//item, read title/description/quant):
  - rates_all.xml: <rss><channel><item> — per-item <pubDate>DD.MM.YYYY</pubDate>
  - get_rates.cfm?fdate: <rates><item>  — channel-level <date>DD.MM.YYYY</date>

On any failure (network, timeout, parse, missing currency) the public helper
falls back to config.FX_TO_KZT (env-overridable) and marks source='fallback'.
A fallback always succeeds — the endpoint never 5xx due to NBK being unavailable.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from xml.etree.ElementTree import Element as _Element

import defusedxml.ElementTree as ET  # safe against XXE / billion-laughs attacks
import httpx

from . import config

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- data types


@dataclass
class RateEntry:
    """A parsed NBK rate for a single currency."""

    code: str
    per_unit_kzt: float  # = raw / quant
    quant: int
    raw: float  # <description> value before dividing by quant


@dataclass
class NbkResult:
    """All currency rates parsed from one NBK feed response."""

    rates: dict[str, RateEntry]  # ISO code (upper) -> RateEntry
    as_of: str | None  # DD.MM.YYYY string from the feed, or None


class NbkError(Exception):
    """Raised when the NBK feed cannot be fetched or parsed."""


# --------------------------------------------------------------------------- XML parsing


def _find_text(el: _Element, path: str) -> str | None:
    """Return stripped text of the first matching sub-element, or None."""
    found = el.find(path)
    if found is not None and found.text:
        return found.text.strip()
    return None


def _parse_nbk_xml(content: bytes) -> NbkResult:
    """Parse NBK RSS XML into a NbkResult.

    Handles both feed shapes:
    - rates_all.xml:    <rss version="2.0"><channel><item>…</item></channel></rss>
                        Per-item <pubDate>DD.MM.YYYY</pubDate>; no <fullname>.
    - get_rates.cfm:    <rates><date>DD.MM.YYYY</date><item>…</item></rates>
                        Channel-level <date>; per-item <fullname>; no per-item pubDate.

    The parser is intentionally lenient: unknown elements are ignored, unparseable
    items are skipped with a debug log (not an error).

    Raises:
        NbkError: if the XML cannot be parsed or contains no <item> elements.
    """
    try:
        root = ET.fromstring(content.decode("utf-8"))
    except (ET.ParseError, UnicodeDecodeError) as exc:
        raise NbkError(f"XML parse error: {exc}") from exc

    # Channel-level date used as fallback when items have no per-item <pubDate>.
    # For get_rates.cfm the <date> is a direct child of <rates> (the root).
    # For rates_all.xml there is no channel-level date — only per-item pubDate.
    channel_date: str | None = _find_text(root, "date") or _find_text(
        root, "channel/date"
    )

    rates: dict[str, RateEntry] = {}
    as_of: str | None = None

    for item in root.iter("item"):
        code = (_find_text(item, "title") or "").strip().upper()
        if not code:
            continue

        # <description> may use a comma as decimal separator; sanitize.
        raw_desc = (_find_text(item, "description") or "").strip().replace(",", ".")
        raw_quant = (_find_text(item, "quant") or "1").strip().replace(",", ".")

        try:
            raw_val = float(raw_desc)
        except ValueError:
            logger.debug(
                "NBK: skipping %s — unparseable <description> %r", code, raw_desc
            )
            continue

        try:
            quant_f = float(raw_quant)
        except ValueError:
            logger.debug(
                "NBK: %s has unparseable <quant> %r — treating as 1", code, raw_quant
            )
            quant_f = 1.0

        # Guard against zero / negative quant.
        if quant_f <= 0:
            logger.debug(
                "NBK: %s has non-positive quant %.3f — treating as 1", code, quant_f
            )
            quant_f = 1.0

        # Round to the nearest integer so the stored quant equals the divisor used
        # in per_unit_kzt — preserving the contract: per_unit_kzt = raw / quant.
        quant_int = int(round(quant_f))
        per_unit = round(raw_val / quant_int, 6)

        # Date: prefer per-item <pubDate> (rates_all.xml), else channel-level.
        item_date = (_find_text(item, "pubDate") or "").strip() or channel_date
        if item_date and as_of is None:
            as_of = item_date

        rates[code] = RateEntry(
            code=code,
            per_unit_kzt=per_unit,
            quant=quant_int,
            raw=raw_val,
        )

    if not rates:
        raise NbkError("no <item> elements found in NBK feed XML")

    return NbkResult(rates=rates, as_of=as_of)


# --------------------------------------------------------------------------- HTTP fetcher


def fetch_nbk_rates(
    date: str | None = None,
    client: httpx.Client | None = None,
) -> NbkResult:
    """Fetch and parse NBK exchange rates over HTTP.

    Args:
        date:   DD.MM.YYYY string for a historical date; None uses the live feed.
        client: Optional pre-built httpx.Client (for tests — allows injection without
                patching the module).  When None, a short-lived client is created.

    Returns:
        NbkResult with all currencies present in the feed.

    Raises:
        NbkError: on any network, HTTP-status, or parse failure.
    """
    if date:
        url = f"{config.NBK_FX_URL}?fdate={date}"
    else:
        url = config.NBK_RATES_ALL_URL

    logger.info("NBK: fetching %s", url)

    def _do(c: httpx.Client) -> NbkResult:
        try:
            response = c.get(url)
        except httpx.HTTPError as exc:
            raise NbkError(
                f"HTTP error fetching NBK feed ({url}): {type(exc).__name__}: {exc}"
            ) from exc

        if response.status_code != 200:
            raise NbkError(
                f"NBK returned HTTP {response.status_code} for {url}: "
                f"{response.text[:300]}"
            )

        # Guard against the known JSON-error body the bare URL returns:
        # {"code":500,"message":"Invalid format date. Correct format is: DD.MM.YYYY"}
        # That body starts with '{' and is not valid XML.
        first_byte = response.content[:1]
        if first_byte == b"{":
            raise NbkError(
                f"NBK returned a JSON error body (expected XML): {response.text[:300]}"
            )

        return _parse_nbk_xml(response.content)

    if client is not None:
        return _do(client)

    with httpx.Client(
        timeout=10.0,
        follow_redirects=True,
        headers={"User-Agent": "medarchive/1.0"},
    ) as c:
        return _do(c)


# --------------------------------------------------------------------------- in-memory cache


@dataclass
class _CacheEntry:
    result: NbkResult
    fetched_at: float  # time.monotonic() timestamp


# Keyed by the `date` parameter (None = latest feed, "DD.MM.YYYY" = historical).
# Historical dates are immutable (NBK never revises them) and can be kept indefinitely.
_cache: dict[str | None, _CacheEntry] = {}
_cache_lock = threading.Lock()


def _cached_fetch(
    date: str | None,
    refresh: bool,
    ttl: float,
    client: httpx.Client | None = None,
) -> NbkResult:
    """Return cached result if within TTL; otherwise fetch and update cache.

    The lock prevents duplicate concurrent fetches in the sync threadpool but
    accepts benign races in the rare window between the read check and the write.
    """
    now = time.monotonic()

    if not refresh:
        with _cache_lock:
            entry = _cache.get(date)
        if entry is not None:
            if date is not None:
                # Historical dates are immutable — NBK never revises them.
                # Skip the TTL check and keep them cached indefinitely.
                logger.debug("NBK cache hit (historical, immutable) for date=%r", date)
                return entry.result
            age = now - entry.fetched_at
            if age < ttl:
                logger.debug(
                    "NBK cache hit for date=%r (age=%.1fs / ttl=%.0fs)", date, age, ttl
                )
                return entry.result

    result = fetch_nbk_rates(date=date, client=client)

    with _cache_lock:
        _cache[date] = _CacheEntry(result=result, fetched_at=time.monotonic())

    return result


# --------------------------------------------------------------------------- last-good store

# The most recent *successful* live (latest-feed) rate, persisted to disk so it survives
# restarts. Used as the fallback when NBK is unreachable — far closer to reality than the
# static config constant (e.g. a stale 525 vs. a real ~486 USD/KZT), minimizing conversion
# error during an outage. Only the latest feed (date=None) updates it; historical lookups
# do not, so it always represents the most recent known spot rate.
_LAST_GOOD_PATH = config.DATA_DIR / "fx_last_good.json"
_last_good: dict[str, Any] | None = None
_last_good_lock = threading.Lock()


def _load_last_good() -> None:
    global _last_good
    try:
        if _LAST_GOOD_PATH.exists():
            _last_good = json.loads(_LAST_GOOD_PATH.read_text(encoding="utf-8"))
            logger.info(
                "loaded last-good FX rate (as_of %s)", (_last_good or {}).get("as_of")
            )
    except Exception as exc:  # noqa: BLE001 — a corrupt cache file must not break startup
        logger.warning("could not load last-good FX rate: %s", exc)
        _last_good = None


def _record_last_good(usd: RateEntry, rub: RateEntry, as_of: str | None) -> None:
    """Remember (and persist) a successful live rate for use as the outage fallback."""
    global _last_good
    data = {
        "usd": {"per_unit_kzt": usd.per_unit_kzt, "quant": usd.quant, "raw": usd.raw},
        "rub": {"per_unit_kzt": rub.per_unit_kzt, "quant": rub.quant, "raw": rub.raw},
        "as_of": as_of,
        "saved_at": time.time(),
    }
    with _last_good_lock:
        prev = _last_good
        _last_good = data
    # Only touch disk when the value actually changed (NBK updates ~once per business day).
    changed = (
        prev is None
        or prev.get("as_of") != as_of
        or prev.get("usd", {}).get("per_unit_kzt") != usd.per_unit_kzt
        or prev.get("rub", {}).get("per_unit_kzt") != rub.per_unit_kzt
    )
    if changed:
        try:
            _LAST_GOOD_PATH.parent.mkdir(parents=True, exist_ok=True)
            _LAST_GOOD_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001 — persistence is best-effort
            logger.warning("could not persist last-good FX rate: %s", exc)


_load_last_good()


# --------------------------------------------------------------------------- public API


@dataclass
class FxPayload:
    """Resolved USD->KZT and RUB->KZT rates with provenance metadata."""

    usd_kzt: float
    rub_kzt: float
    source: str  # "nbk" | "fallback"
    as_of: str | None  # DD.MM.YYYY from the NBK feed, or None on fallback
    stale: bool  # True when fallback config values are served
    error: str | None  # human-readable reason when source == "fallback"
    usd_entry: RateEntry | None = field(default=None)
    rub_entry: RateEntry | None = field(default=None)


def get_fx_rates(
    date: str | None = None,
    refresh: bool = False,
    client: httpx.Client | None = None,
) -> FxPayload:
    """Return USD->KZT and RUB->KZT rates — never raises.

    Tries the live NBK feed first.  On ANY failure (network/timeout/parse/missing
    currency) falls back to config.FX_TO_KZT (env-overridable via FX_USD_KZT /
    FX_RUB_KZT) with source='fallback'.

    Args:
        date:    DD.MM.YYYY for historical data; None for the latest feed.
        refresh: Bypass the in-memory cache and force a fresh fetch.
        client:  Optional httpx.Client for testing (injection without patching).

    Returns:
        FxPayload — always populated; source indicates live vs. fallback.
    """
    ttl = float(config.NBK_CACHE_TTL)

    try:
        result = _cached_fetch(date=date, refresh=refresh, ttl=ttl, client=client)
    except NbkError as exc:
        logger.warning("NBK fetch failed, using fallback rates: %s", exc)
        return _make_fallback(str(exc))
    except Exception as exc:  # noqa: BLE001 — ensure nothing escapes the endpoint
        logger.error(
            "Unexpected error fetching NBK rates: %s: %s", type(exc).__name__, exc
        )
        return _make_fallback(f"{type(exc).__name__}: {exc}")

    usd_entry = result.rates.get("USD")
    rub_entry = result.rates.get("RUB")
    missing = [c for c, e in [("USD", usd_entry), ("RUB", rub_entry)] if e is None]

    if missing:
        err = f"currencies missing from NBK feed: {', '.join(missing)}"
        logger.warning("NBK: %s — using fallback", err)
        return _make_fallback(err)

    # At this point both usd_entry and rub_entry are non-None.
    assert usd_entry is not None  # mypy / pyright hint
    assert rub_entry is not None

    # Remember this live latest-feed rate as the outage fallback (date=None only, so the
    # store always holds the most recent spot rate, never a historical one).
    if date is None:
        _record_last_good(usd_entry, rub_entry, result.as_of)

    return FxPayload(
        usd_kzt=usd_entry.per_unit_kzt,
        rub_kzt=rub_entry.per_unit_kzt,
        source="nbk",
        as_of=result.as_of,
        stale=False,
        error=None,
        usd_entry=usd_entry,
        rub_entry=rub_entry,
    )


def _make_fallback(error: str) -> FxPayload:
    """Fallback rate when NBK is unreachable.

    Prefers the last successfully-fetched NBK rate (source='last_good') — closest to the
    real rate — and only drops to the static config.FX_TO_KZT defaults (source='fallback')
    when NBK has never been reached on this host.
    """
    with _last_good_lock:
        lg = dict(_last_good) if _last_good else None

    if lg and "usd" in lg and "rub" in lg:
        usd = RateEntry(
            "USD", lg["usd"]["per_unit_kzt"], int(lg["usd"]["quant"]), lg["usd"]["raw"]
        )
        rub = RateEntry(
            "RUB", lg["rub"]["per_unit_kzt"], int(lg["rub"]["quant"]), lg["rub"]["raw"]
        )
        return FxPayload(
            usd_kzt=usd.per_unit_kzt,
            rub_kzt=rub.per_unit_kzt,
            source="last_good",
            as_of=lg.get("as_of"),
            stale=True,
            error=f"{error}; serving last NBK rate from {lg.get('as_of')}",
            usd_entry=usd,
            rub_entry=rub,
        )

    return FxPayload(
        usd_kzt=config.FX_TO_KZT.get("USD", 525.0),
        rub_kzt=config.FX_TO_KZT.get("RUB", 5.7),
        source="fallback",
        as_of=None,
        stale=True,
        error=error,
        usd_entry=None,
        rub_entry=None,
    )


def cache_info() -> dict[str, Any]:
    """Return diagnostic information about the in-memory rate cache."""
    with _cache_lock:
        now = time.monotonic()
        ttl = float(config.NBK_CACHE_TTL)
        entries: dict[str, Any] = {}
        for key, entry in _cache.items():
            age = now - entry.fetched_at
            entries[str(key)] = {
                "age_sec": round(age, 1),
                "expires_in_sec": round(max(0.0, ttl - age), 1),
                "as_of": entry.result.as_of,
                "currencies": sorted(entry.result.rates.keys()),
            }
    with _last_good_lock:
        lg = dict(_last_good) if _last_good else None
    last_good = (
        {
            "as_of": lg.get("as_of"),
            "usd_kzt": lg.get("usd", {}).get("per_unit_kzt"),
            "rub_kzt": lg.get("rub", {}).get("per_unit_kzt"),
        }
        if lg
        else None
    )
    return {
        "ttl_sec": ttl,
        "entry_count": len(entries),
        "entries": entries,
        "last_good": last_good,
    }
