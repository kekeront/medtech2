#!/usr/bin/env python3
"""Tests for app/fx.py and the GET /fx/rates + GET /fx/health endpoints.

Design:
  - NO live network calls.  Every test injects a fake httpx client or patches
    app.fx.fetch_nbk_rates so the real httpx.Client is never instantiated.
  - Follows tests/test_parser_db.py style: a Checker harness, __main__ entry point,
    and pytest-discoverable test_ functions.

Run:
    uv run python tests/test_fx.py
    uv run pytest tests/test_fx.py -q
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Allow plain-script execution from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ------------------------------------------------------------------ sample XML fixtures

# rates_all.xml shape: RSS 2.0, per-item <pubDate>, no <fullname>
# Includes AMD with quant=10 to prove the quant-division path.
_RATES_ALL_XML = b"""\
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>National Bank of Kazakhstan</title>
    <item>
      <title>USD</title>
      <pubDate>27.06.2026</pubDate>
      <description>486.47</description>
      <quant>1</quant>
      <index>UP</index>
      <change>+1.07</change>
    </item>
    <item>
      <title>RUB</title>
      <pubDate>27.06.2026</pubDate>
      <description>6.26</description>
      <quant>1</quant>
      <index>DOWN</index>
      <change>-0.15</change>
    </item>
    <item>
      <title>AMD</title>
      <pubDate>27.06.2026</pubDate>
      <description>13.23</description>
      <quant>10</quant>
      <index>UP</index>
      <change>+0.01</change>
    </item>
  </channel>
</rss>
"""

# get_rates.cfm?fdate shape: <rates> root, channel-level <date>, per-item <fullname>
_HISTORICAL_XML = b"""\
<?xml version="1.0" encoding="utf-8"?>
<rates>
  <date>26.06.2026</date>
  <item>
    <title>USD</title>
    <description>485.40</description>
    <quant>1</quant>
    <fullname>\xd0\x94\xd0\xbe\xd0\xbb\xd0\xbb\xd0\xb0\xd1\x80 \xd0\xa1\xd0\xa8\xd0\x90</fullname>
  </item>
  <item>
    <title>RUB</title>
    <description>6.41</description>
    <quant>1</quant>
    <fullname>\xd0\xa0\xd0\xbe\xd1\x81\xd1\x81\xd0\xb8\xd0\xb9\xd1\x81\xd0\xba\xd0\xb8\xd0\xb9 \xd1\x80\xd1\x83\xd0\xb1\xd0\xbb\xd1\x8c</fullname>
  </item>
  <item>
    <title>AMD</title>
    <description>13.10</description>
    <quant>10</quant>
    <fullname>\xd0\x90\xd1\x80\xd0\xbc\xd1\x8f\xd0\xbd\xd1\x81\xd0\xba\xd0\xb8\xd0\xb9 \xd0\xb4\xd1\x80\xd0\xb0\xd0\xbc</fullname>
  </item>
</rates>
"""

# Missing USD and RUB (only AMD).
_MISSING_CURRENCIES_XML = b"""\
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>AMD</title>
      <pubDate>27.06.2026</pubDate>
      <description>13.23</description>
      <quant>10</quant>
    </item>
  </channel>
</rss>
"""

# JSON error body the bare get_rates.cfm URL returns (not valid XML).
_JSON_ERROR_BODY = b'{"code":500,"message":"Invalid format date. Correct format is: 27.06.2026 or 27/06/2026"}'

# ------------------------------------------------------------------ fake httpx helpers


class _FakeResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(
        self, content: bytes, status_code: int = 200, content_type: str = "text/xml"
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.text = content.decode("utf-8", errors="replace")
        self.headers: dict[str, str] = {"content-type": content_type}


class _FakeClient:
    """Fake httpx.Client that returns a pre-configured response.

    Counts calls so caching tests can assert network hits.
    """

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.call_count = 0

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def get(self, _url: str, **_kw: object) -> _FakeResponse:
        self.call_count += 1
        return self._response

    def head(self, _url: str, **_kw: object) -> _FakeResponse:
        self.call_count += 1
        return self._response


# ------------------------------------------------------------------ Checker harness

_TTY = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


class Checker:
    """Minimal PASS/FAIL harness — keeps the test file dependency-free."""

    def __init__(self) -> None:
        self.failures = 0
        self.passed = 0
        self._section = ""

    def section(self, title: str) -> None:
        self._section = title
        print(f"\n{_c('1;36', '== ' + title + ' ==')}")

    def check(self, ok: bool, name: str, detail: str = "") -> bool:
        if ok:
            self.passed += 1
            print(f"  {_c('32', 'PASS')}  {name}")
        else:
            self.failures += 1
            print(f"  {_c('31', 'FAIL')}  {name}" + (f"  — {detail}" if detail else ""))
        return ok

    def summary(self) -> int:
        print(f"\n{_c('1', '─' * 60)}")
        print(
            f"  {_c('32', str(self.passed) + ' passed')}, "
            f"{_c('31', str(self.failures) + ' failed')}"
        )
        if self.failures:
            print(_c("1;31", "  RESULT: FAIL"))
            return 1
        print(_c("1;32", "  RESULT: PASS"))
        return 0


# ------------------------------------------------------------------ helpers


def _clear_cache() -> None:
    """Reset the module-level NBK cache between tests."""
    from app.fx import _cache

    _cache.clear()


# ================================================================== unit tests


def test_parse_rates_all_xml(c: Checker) -> None:
    """Happy path: rates_all.xml format parses USD, RUB, AMD correctly."""
    c.section("parse_nbk_xml — rates_all.xml format")
    from app.fx import _parse_nbk_xml

    result = _parse_nbk_xml(_RATES_ALL_XML)

    c.check("USD" in result.rates, "USD present")
    c.check("RUB" in result.rates, "RUB present")
    c.check("AMD" in result.rates, "AMD present")

    usd = result.rates["USD"]
    c.check(
        usd.per_unit_kzt == 486.47, "USD per_unit_kzt = 486.47", str(usd.per_unit_kzt)
    )
    c.check(usd.quant == 1, "USD quant = 1")
    c.check(usd.raw == 486.47, "USD raw = 486.47")

    rub = result.rates["RUB"]
    c.check(
        abs(rub.per_unit_kzt - 6.26) < 0.0001,
        "RUB per_unit_kzt = 6.26",
        str(rub.per_unit_kzt),
    )

    amd = result.rates["AMD"]
    expected_amd = round(13.23 / 10, 6)
    c.check(
        abs(amd.per_unit_kzt - expected_amd) < 0.000001,
        f"AMD per_unit_kzt = 13.23/10 = {expected_amd}",
        str(amd.per_unit_kzt),
    )
    c.check(amd.quant == 10, "AMD quant = 10")
    c.check(amd.raw == 13.23, "AMD raw = 13.23")

    c.check(result.as_of == "27.06.2026", "as_of = '27.06.2026'", str(result.as_of))


def test_parse_historical_xml(c: Checker) -> None:
    """Historical feed (get_rates.cfm?fdate) uses channel-level <date>, has <fullname>."""
    c.section("parse_nbk_xml — historical get_rates.cfm format")
    from app.fx import _parse_nbk_xml

    result = _parse_nbk_xml(_HISTORICAL_XML)

    c.check("USD" in result.rates, "USD present")
    c.check("RUB" in result.rates, "RUB present")

    usd = result.rates["USD"]
    c.check(
        abs(usd.per_unit_kzt - 485.40) < 0.0001,
        "USD per_unit_kzt = 485.40",
        str(usd.per_unit_kzt),
    )

    # Channel-level date extracted from <rates><date>.
    c.check(
        result.as_of == "26.06.2026",
        "as_of from channel-level <date>",
        str(result.as_of),
    )

    # Cyrillic fullname doesn't break parsing.
    c.check("AMD" in result.rates, "AMD with Cyrillic fullname parsed OK")
    amd = result.rates["AMD"]
    expected = round(13.10 / 10, 6)
    c.check(abs(amd.per_unit_kzt - expected) < 0.000001, f"AMD = 13.10/10 = {expected}")


def test_quant_division(c: Checker) -> None:
    """CRITICAL: per_unit_kzt = description / quant — never assume quant == 1."""
    c.section("quant division correctness")
    from app.fx import _parse_nbk_xml

    xml = b"""\
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <item><title>AMD</title><pubDate>27.06.2026</pubDate><description>13.23</description><quant>10</quant></item>
  <item><title>JPY</title><pubDate>27.06.2026</pubDate><description>3.42</description><quant>100</quant></item>
  <item><title>USD</title><pubDate>27.06.2026</pubDate><description>486.47</description><quant>1</quant></item>
  <item><title>RUB</title><pubDate>27.06.2026</pubDate><description>6.26</description><quant>1</quant></item>
</channel></rss>
"""
    result = _parse_nbk_xml(xml)
    amd = result.rates["AMD"]
    jpy = result.rates["JPY"]
    usd = result.rates["USD"]

    c.check(
        abs(amd.per_unit_kzt - 1.323) < 0.001,
        "AMD: 13.23/10 = 1.323",
        str(amd.per_unit_kzt),
    )
    c.check(
        abs(jpy.per_unit_kzt - 0.0342) < 0.0001,
        "JPY: 3.42/100 = 0.0342",
        str(jpy.per_unit_kzt),
    )
    c.check(usd.per_unit_kzt == 486.47, "USD quant=1 unchanged")

    # Direction sanity: USD should be hundreds, RUB single digits.
    c.check(usd.per_unit_kzt > 50, f"USD per_unit_kzt > 50 (got {usd.per_unit_kzt})")
    c.check(result.rates["RUB"].per_unit_kzt < 50, "RUB per_unit_kzt < 50")


def test_quant_edge_cases(c: Checker) -> None:
    """quant=0, empty, missing, or non-numeric -> treated as 1.0, no crash."""
    c.section("quant edge cases")
    from app.fx import _parse_nbk_xml

    xml = b"""\
<?xml version="1.0" encoding="utf-8"?>
<rates>
  <date>27.06.2026</date>
  <item><title>USD</title><description>486.47</description><quant>0</quant></item>
  <item><title>RUB</title><description>6.26</description><quant></quant></item>
  <item><title>EUR</title><description>540.00</description></item>
  <item><title>GBP</title><description>620.00</description><quant>abc</quant></item>
</rates>
"""
    result = _parse_nbk_xml(xml)

    # All should produce the raw value (treated as quant=1).
    usd = result.rates.get("USD")
    c.check(usd is not None, "quant=0 item not dropped")
    if usd:
        c.check(usd.per_unit_kzt == 486.47, "quant=0 -> treated as 1 (486.47/1=486.47)")

    rub = result.rates.get("RUB")
    c.check(rub is not None, "empty quant item not dropped")
    if rub:
        c.check(abs(rub.per_unit_kzt - 6.26) < 0.0001, "empty quant -> treated as 1")

    eur = result.rates.get("EUR")
    c.check(eur is not None, "missing quant item not dropped")
    if eur:
        c.check(abs(eur.per_unit_kzt - 540.0) < 0.0001, "missing quant -> treated as 1")

    gbp = result.rates.get("GBP")
    c.check(gbp is not None, "non-numeric quant item not dropped")
    if gbp:
        c.check(
            abs(gbp.per_unit_kzt - 620.0) < 0.0001, "non-numeric quant -> treated as 1"
        )


def test_description_comma_decimal(c: Checker) -> None:
    """description with comma decimal ('486,47') is normalised to '486.47'."""
    c.section("comma decimal in description")
    from app.fx import _parse_nbk_xml

    xml = b"""\
<?xml version="1.0" encoding="utf-8"?>
<rates>
  <date>27.06.2026</date>
  <item><title>USD</title><description>486,47</description><quant>1</quant></item>
  <item><title>RUB</title><description>6,26</description><quant>1</quant></item>
</rates>
"""
    result = _parse_nbk_xml(xml)
    usd = result.rates.get("USD")
    c.check(usd is not None, "USD with comma decimal parsed")
    if usd:
        c.check(abs(usd.per_unit_kzt - 486.47) < 0.001, "486,47 -> 486.47")


def test_malformed_xml_fallback(c: Checker) -> None:
    """Malformed/truncated XML raises NbkError; get_fx_rates falls back to config."""
    c.section("malformed XML -> fallback")
    from app import config
    from app.fx import get_fx_rates

    _clear_cache()
    malformed = b"<not valid xml>>"
    fake_client = _FakeClient(_FakeResponse(malformed))

    payload = get_fx_rates(refresh=True, client=fake_client)

    c.check(payload.source == "fallback", "source = 'fallback'")
    c.check(payload.stale is True, "stale = True")
    c.check(payload.error is not None, "error message set")
    c.check(
        payload.usd_kzt == config.FX_TO_KZT["USD"], "USD falls back to config value"
    )
    c.check(
        payload.rub_kzt == config.FX_TO_KZT["RUB"], "RUB falls back to config value"
    )


def test_json_error_body_fallback(c: Checker) -> None:
    """NBK JSON-error body (starts with '{') -> NbkError -> fallback, not a crash."""
    c.section("JSON error body -> fallback")
    from app.fx import get_fx_rates

    _clear_cache()
    fake_client = _FakeClient(_FakeResponse(_JSON_ERROR_BODY, status_code=200))

    payload = get_fx_rates(refresh=True, client=fake_client)

    c.check(payload.source == "fallback", "source = 'fallback'")
    c.check(payload.stale is True, "stale = True")
    c.check(payload.error is not None, "error set")


def test_http_500_fallback(c: Checker) -> None:
    """Non-200 HTTP status (e.g. 500) -> NbkError -> fallback."""
    c.section("HTTP 500 -> fallback")
    from app.fx import get_fx_rates

    _clear_cache()
    fake_client = _FakeClient(_FakeResponse(b"Server Error", status_code=500))

    payload = get_fx_rates(refresh=True, client=fake_client)

    c.check(payload.source == "fallback", "source = 'fallback'")
    c.check(payload.stale is True, "stale = True")


def test_network_failure_fallback(c: Checker) -> None:
    """httpx.ConnectError / network failures -> fallback, no exception escapes."""
    c.section("network failure -> fallback")
    import httpx

    from app import config
    from app.fx import get_fx_rates

    _clear_cache()

    class _ErrorClient:
        call_count = 0

        def __enter__(self) -> _ErrorClient:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def get(self, _url: str, **_kw: object) -> None:
            _ErrorClient.call_count += 1
            raise httpx.ConnectError("connection refused")

    payload = get_fx_rates(refresh=True, client=_ErrorClient())  # type: ignore[arg-type]

    c.check(payload.source == "fallback", "source = 'fallback'")
    c.check(payload.stale is True, "stale = True")
    c.check(payload.error is not None, "error message describes the failure")
    c.check(config.FX_TO_KZT["USD"] == payload.usd_kzt, "USD = config fallback")
    c.check(config.FX_TO_KZT["RUB"] == payload.rub_kzt, "RUB = config fallback")


def test_missing_currencies_fallback(c: Checker) -> None:
    """Feed that has no USD or RUB items -> fallback for the whole payload."""
    c.section("missing USD/RUB in feed -> fallback")
    from app.fx import get_fx_rates

    _clear_cache()
    fake_client = _FakeClient(_FakeResponse(_MISSING_CURRENCIES_XML))

    payload = get_fx_rates(refresh=True, client=fake_client)

    c.check(payload.source == "fallback", "source = 'fallback'")
    c.check(payload.error is not None, "error names the missing currencies")
    if payload.error:
        c.check(
            "USD" in payload.error or "RUB" in payload.error, "error mentions USD/RUB"
        )


def test_cache_hit(c: Checker) -> None:
    """Second call within TTL reuses cache; network is hit exactly once."""
    c.section("caching — hit within TTL")
    from app.fx import get_fx_rates

    _clear_cache()
    fake_client = _FakeClient(_FakeResponse(_RATES_ALL_XML))

    # First call — cache miss, one network hit.
    p1 = get_fx_rates(refresh=False, client=fake_client)
    # Second call — same client, should use cache.
    p2 = get_fx_rates(refresh=False, client=fake_client)

    c.check(
        fake_client.call_count == 1,
        f"network called once (got {fake_client.call_count})",
    )
    c.check(p1.source == "nbk", "first call source = 'nbk'")
    c.check(p2.source == "nbk", "second call source = 'nbk' (from cache)")
    c.check(p1.usd_kzt == p2.usd_kzt, "both calls return identical USD rate")


def test_cache_refresh(c: Checker) -> None:
    """refresh=True bypasses the cache and forces a new fetch."""
    c.section("caching — refresh=True busts cache")
    from app.fx import get_fx_rates

    _clear_cache()
    fake_client = _FakeClient(_FakeResponse(_RATES_ALL_XML))

    get_fx_rates(refresh=False, client=fake_client)  # populate cache
    get_fx_rates(refresh=True, client=fake_client)  # should re-fetch

    c.check(
        fake_client.call_count == 2,
        f"network called twice with refresh=True (got {fake_client.call_count})",
    )


def test_cache_ttl_expiry(c: Checker) -> None:
    """After TTL expires the cache is considered stale and a re-fetch is triggered."""
    c.section("caching — TTL expiry triggers re-fetch")
    from app.fx import NbkResult, RateEntry, _cache, _CacheEntry

    _clear_cache()

    # Manually insert a cache entry with a very old fetched_at.
    old_result = NbkResult(
        rates={
            "USD": RateEntry(code="USD", per_unit_kzt=400.0, quant=1, raw=400.0),
            "RUB": RateEntry(code="RUB", per_unit_kzt=5.0, quant=1, raw=5.0),
        },
        as_of="01.01.2025",
    )
    _cache[None] = _CacheEntry(result=old_result, fetched_at=time.monotonic() - 7200.0)

    # Fresh fetch should replace the stale entry.
    fake_client = _FakeClient(_FakeResponse(_RATES_ALL_XML))
    from app.fx import get_fx_rates

    payload = get_fx_rates(refresh=False, client=fake_client)

    c.check(fake_client.call_count == 1, "re-fetched after TTL expired")
    c.check(abs(payload.usd_kzt - 486.47) < 0.001, "new USD rate from fresh fetch")


def test_date_param_routes_to_cfm(c: Checker) -> None:
    """date=DD.MM.YYYY uses get_rates.cfm?fdate URL and parses the historical XML."""
    c.section("date param — historical feed shape")
    from app.fx import fetch_nbk_rates

    _clear_cache()
    captured_url: list[str] = []

    class _CapturingClient:
        def get(self, url: str, **_kw: object) -> _FakeResponse:
            captured_url.append(url)
            return _FakeResponse(_HISTORICAL_XML)

    result = fetch_nbk_rates(date="26.06.2026", client=_CapturingClient())  # type: ignore[arg-type]

    c.check(len(captured_url) == 1, "exactly one HTTP request made")
    if captured_url:
        c.check(
            "fdate=26.06.2026" in captured_url[0],
            f"URL contains fdate param: {captured_url[0]}",
        )
    c.check("USD" in result.rates, "USD parsed from historical feed")
    c.check(
        result.as_of == "26.06.2026",
        "as_of from channel-level <date>",
        str(result.as_of),
    )


# ================================================================== FastAPI endpoint tests


def test_fastapi_rates_happy(c: Checker) -> None:
    """GET /fx/rates returns 200 with nbk source when NBK succeeds."""
    c.section("GET /fx/rates — happy path via TestClient")
    from fastapi.testclient import TestClient

    from app.api import app
    from app.fx import NbkResult, RateEntry

    _clear_cache()

    good_result = NbkResult(
        rates={
            "USD": RateEntry(code="USD", per_unit_kzt=486.47, quant=1, raw=486.47),
            "RUB": RateEntry(code="RUB", per_unit_kzt=6.26, quant=1, raw=6.26),
        },
        as_of="27.06.2026",
    )

    with patch("app.fx.fetch_nbk_rates", return_value=good_result):
        with TestClient(app) as client:
            r = client.get("/fx/rates")

    c.check(r.status_code == 200, f"GET /fx/rates -> 200 (got {r.status_code})")
    if r.status_code == 200:
        body = r.json()
        c.check(body["source"] == "nbk", f"source='nbk' (got {body.get('source')})")
        c.check(body["base"] == "KZT", "base='KZT'")
        c.check(body["stale"] is False, "stale=False")
        c.check(
            body["as_of"] == "27.06.2026",
            f"as_of='27.06.2026' (got {body.get('as_of')})",
        )
        usd = body["rates"]["USD"]
        c.check(
            abs(usd["per_unit_kzt"] - 486.47) < 0.001,
            f"USD per_unit_kzt~486.47 (got {usd['per_unit_kzt']})",
        )
        c.check(usd["quant"] == 1, "USD quant=1")
        rub = body["rates"]["RUB"]
        c.check(
            abs(rub["per_unit_kzt"] - 6.26) < 0.001,
            f"RUB per_unit_kzt~6.26 (got {rub['per_unit_kzt']})",
        )
        # Direction sanity.
        c.check(usd["per_unit_kzt"] > 50, "USD per_unit_kzt > 50 (not inverted)")
        c.check(rub["per_unit_kzt"] < 50, "RUB per_unit_kzt < 50 (not inverted)")


def test_fastapi_rates_fallback(c: Checker) -> None:
    """GET /fx/rates returns 200 with fallback source when NBK fails."""
    c.section("GET /fx/rates — NBK failure falls back (no 5xx)")
    import httpx as _httpx
    from fastapi.testclient import TestClient

    from app import config
    from app.api import app

    _clear_cache()

    with patch("app.fx.fetch_nbk_rates", side_effect=_httpx.ConnectError("timeout")):
        with TestClient(app) as client:
            r = client.get("/fx/rates")

    c.check(
        r.status_code == 200,
        f"GET /fx/rates -> 200 even on NBK failure (got {r.status_code})",
    )
    if r.status_code == 200:
        body = r.json()
        c.check(
            body["source"] == "fallback",
            f"source='fallback' (got {body.get('source')})",
        )
        c.check(body["stale"] is True, "stale=True")
        c.check(body["error"] is not None, "error field populated")
        c.check(
            abs(body["rates"]["USD"]["per_unit_kzt"] - config.FX_TO_KZT["USD"]) < 0.001,
            "USD = config fallback value",
        )


def test_fastapi_rates_bad_date(c: Checker) -> None:
    """GET /fx/rates?date with invalid format returns 422, does NOT call NBK."""
    c.section("GET /fx/rates — invalid date format -> 422")
    from fastapi.testclient import TestClient

    from app.api import app

    _clear_cache()

    bad_dates = ["2026-06-27", "06.26.2026", "27/06/2026", "garbage", "00.00.0000"]

    with patch("app.fx.fetch_nbk_rates") as mock_fetch:
        with TestClient(app) as client:
            for bad in bad_dates:
                r = client.get("/fx/rates", params={"date": bad})
                c.check(
                    r.status_code == 422,
                    f"date={bad!r} -> 422 (got {r.status_code})",
                )
        # NBK must never be called with a bad date.
        c.check(mock_fetch.call_count == 0, "fetch_nbk_rates not called for bad dates")


def test_fastapi_rates_valid_date(c: Checker) -> None:
    """GET /fx/rates?date=DD.MM.YYYY with a valid date is forwarded to NBK."""
    c.section("GET /fx/rates — valid DD.MM.YYYY date accepted")
    from fastapi.testclient import TestClient

    from app.api import app
    from app.fx import NbkResult, RateEntry

    _clear_cache()

    hist_result = NbkResult(
        rates={
            "USD": RateEntry(code="USD", per_unit_kzt=485.40, quant=1, raw=485.40),
            "RUB": RateEntry(code="RUB", per_unit_kzt=6.41, quant=1, raw=6.41),
        },
        as_of="26.06.2026",
    )

    with patch("app.fx.fetch_nbk_rates", return_value=hist_result) as mock_fetch:
        with TestClient(app) as client:
            r = client.get("/fx/rates", params={"date": "26.06.2026"})

    c.check(r.status_code == 200, f"valid date -> 200 (got {r.status_code})")
    if r.status_code == 200:
        body = r.json()
        c.check(body["as_of"] == "26.06.2026", "as_of matches requested date")
        c.check(mock_fetch.call_count >= 1, "NBK was called")


def test_fastapi_health(c: Checker) -> None:
    """GET /fx/health returns 200 with expected keys."""
    c.section("GET /fx/health")
    from fastapi.testclient import TestClient

    from app.api import app

    # Patch the httpx.Client used inside the health handler to avoid a live network call.
    mock_head_response = MagicMock()
    mock_head_response.status_code = 200

    mock_http_client = MagicMock()
    mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
    mock_http_client.__exit__ = MagicMock(return_value=False)
    mock_http_client.head = MagicMock(return_value=mock_head_response)

    with patch("app.fx_api.httpx.Client", return_value=mock_http_client):
        with TestClient(app) as client:
            r = client.get("/fx/health")

    c.check(r.status_code == 200, f"GET /fx/health -> 200 (got {r.status_code})")
    if r.status_code == 200:
        body = r.json()
        c.check("nbk_reachable" in body, "response has nbk_reachable")
        c.check("cache" in body, "response has cache")
        c.check("ttl_sec" in body.get("cache", {}), "cache has ttl_sec")
        c.check(body["nbk_reachable"] is True, "nbk_reachable=True (mocked 200)")


# ================================================================== driver


def run_all() -> Checker:
    c = Checker()

    test_parse_rates_all_xml(c)
    test_parse_historical_xml(c)
    test_quant_division(c)
    test_quant_edge_cases(c)
    test_description_comma_decimal(c)
    test_malformed_xml_fallback(c)
    test_json_error_body_fallback(c)
    test_http_500_fallback(c)
    test_network_failure_fallback(c)
    test_missing_currencies_fallback(c)
    test_cache_hit(c)
    test_cache_refresh(c)
    test_cache_ttl_expiry(c)
    test_date_param_routes_to_cfm(c)
    test_fastapi_rates_happy(c)
    test_fastapi_rates_fallback(c)
    test_fastapi_rates_bad_date(c)
    test_fastapi_rates_valid_date(c)
    test_fastapi_health(c)

    return c


# pytest-discoverable entry points (one per logical group).
def test_unit_parsing() -> None:
    c = Checker()
    test_parse_rates_all_xml(c)
    test_parse_historical_xml(c)
    test_quant_division(c)
    test_quant_edge_cases(c)
    test_description_comma_decimal(c)
    assert c.failures == 0, f"{c.failures} unit parsing test(s) failed"


def test_unit_fallback() -> None:
    c = Checker()
    test_malformed_xml_fallback(c)
    test_json_error_body_fallback(c)
    test_http_500_fallback(c)
    test_network_failure_fallback(c)
    test_missing_currencies_fallback(c)
    assert c.failures == 0, f"{c.failures} fallback test(s) failed"


def test_unit_caching() -> None:
    c = Checker()
    test_cache_hit(c)
    test_cache_refresh(c)
    test_cache_ttl_expiry(c)
    assert c.failures == 0, f"{c.failures} caching test(s) failed"


def test_api_endpoints() -> None:
    c = Checker()
    test_date_param_routes_to_cfm(c)
    test_fastapi_rates_happy(c)
    test_fastapi_rates_fallback(c)
    test_fastapi_rates_bad_date(c)
    test_fastapi_rates_valid_date(c)
    test_fastapi_health(c)
    assert c.failures == 0, f"{c.failures} API endpoint test(s) failed"


if __name__ == "__main__":
    raise SystemExit(run_all().summary())
