"""Diff parser output against the golden reference and score accuracy.

For every golden row we locate the matching parser row (by normalized name, with
the source code as a tiebreaker) and compare the fields that matter:

  * resident price — STRICT: must equal the golden value.
  * foreign price  — if the golden recorded one, it must appear among ANY tier the
    parser captured (nonresident OR price_extra_tiers). This is deliberate: the 2-field
    schema plus a `price_extra_tiers` map keeps 3+-tier lists lossless, and the golden
    extractors disagreed on which foreign tier to call "nonresident", so we only require
    that the value was captured *somewhere* (i.e. no tier was silently dropped).
  * currency.

A row is *correct* only if it was extracted AND every applicable field matches. Rows the
parser got wrong are additionally checked for whether it flagged them `needs_review` — an
unflagged wrong row is a silent error (the dangerous kind); a flagged one at least lands
in the manual queue, the acceptable failure mode per the ТЗ.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from app.models import PriceItem
from app.normalize import _key
from app.tariffs import current_rates

from .golden import GoldenFile, GoldenRow

PRICE_TOL = 0.5  # KZT; equal prices may differ only by rounding


def _golden_kzt(value: float | None, currency: str) -> float | None:
    """Golden prices are recorded in the source currency; the parser stores KZT.
    Convert with the same live NBK rates the pipeline uses so the two are comparable."""
    if value is None:
        return None
    return round(value * current_rates().get(currency, 1.0), 2)


# Cyrillic letters that look like Latin ones — codes mix them (А02 vs A02).
_CODE_FOLD = str.maketrans("АВЕКМНОРСТХаосрех", "ABEKMHOPCTXaocpex")


def _codenorm(code: str | None) -> str:
    if not code:
        return ""
    c = code.translate(_CODE_FOLD).upper()
    return re.sub(r"[^A-Z0-9.]", "", c)


def _close(a: float | None, b: float | None) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= PRICE_TOL


@dataclass
class RowResult:
    golden: GoldenRow
    status: str  # ok | wrong_resident | wrong_nonresident | wrong_currency | missing
    issues: list[str] = field(default_factory=list)
    matched: bool = False
    flagged: bool = False  # parser set needs_review on the matched row
    parser_resident: float | None = None
    parser_nonresident: float | None = None
    parser_name: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def silent_error(self) -> bool:
        """A wrong row the parser did NOT flag for review — the dangerous case."""
        return self.matched and not self.ok and not self.flagged


@dataclass
class FileScore:
    source_file: str
    partner: str
    total: int
    rows: list[RowResult]

    @property
    def correct(self) -> int:
        return sum(r.ok for r in self.rows)

    @property
    def missing(self) -> int:
        return sum(r.status == "missing" for r in self.rows)

    @property
    def silent_errors(self) -> int:
        return sum(r.silent_error for r in self.rows)

    @property
    def flagged_errors(self) -> int:
        return sum(r.matched and not r.ok and r.flagged for r in self.rows)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def effective_accuracy(self) -> float:
        """Correct OR correctly routed to manual review (acceptable per ТЗ)."""
        good = sum(r.ok or (not r.ok and r.matched and r.flagged) for r in self.rows)
        return good / self.total if self.total else 0.0


def _index(items: list[PriceItem]) -> tuple[dict, dict]:
    by_norm: dict[str, list[PriceItem]] = defaultdict(list)
    by_code: dict[str, list[PriceItem]] = defaultdict(list)
    for it in items:
        by_norm[_key(it.service_name_raw)].append(it)
        if it.service_code_source:
            by_code[_codenorm(it.service_code_source)].append(it)
    return by_norm, by_code


def _pick(cands: list[PriceItem], g: GoldenRow) -> PriceItem | None:
    """Among same-name candidates, prefer same code, then the closest resident price."""
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    gcode = _codenorm(g.code)
    if gcode:
        same = [it for it in cands if _codenorm(it.service_code_source) == gcode]
        if same:
            cands = same
    target = _golden_kzt(g.price_resident_kzt, g.currency_original)
    if target is not None:
        return min(
            cands,
            key=lambda it: abs(float(it.price_resident_kzt or 1e18) - target),
        )
    return cands[0]


def _match(g: GoldenRow, by_norm: dict, by_code: dict) -> PriceItem | None:
    cands = by_norm.get(_key(g.name)) or []
    if not cands and g.code:
        cands = by_code.get(_codenorm(g.code)) or []
    return _pick(cands, g)


def compare(golden: GoldenFile, items: list[PriceItem]) -> FileScore:
    """Score one source file's golden rows against the parser's items for it."""
    by_norm, by_code = _index(items)
    results: list[RowResult] = []

    for g in golden.rows:
        item = _match(g, by_norm, by_code)
        if item is None:
            results.append(
                RowResult(golden=g, status="missing", issues=["not extracted"])
            )
            continue

        res = (
            float(item.price_resident_kzt)
            if item.price_resident_kzt is not None
            else None
        )
        non = (
            float(item.price_nonresident_kzt)
            if item.price_nonresident_kzt is not None
            else None
        )
        # Every foreign tier the parser captured for this row (nonresident + extras).
        foreign = {
            round(float(v), 2)
            for v in (non, *(item.price_extra_tiers or {}).values())
            if v is not None
        }

        g_res = _golden_kzt(g.price_resident_kzt, g.currency_original)
        g_non = _golden_kzt(g.price_nonresident_kzt, g.currency_original)
        issues: list[str] = []
        if not _close(res, g_res):
            issues.append(f"resident {res} != {g_res}")
        # Foreign price must be captured somewhere (no tier silently dropped).
        if g_non is not None and not any(_close(g_non, f) for f in foreign):
            issues.append(f"foreign {g_non} not in captured tiers {sorted(foreign)}")
        if item.currency_original != g.currency_original:
            issues.append(f"currency {item.currency_original} != {g.currency_original}")

        if not issues:
            status = "ok"
        elif issues[0].startswith("resident"):
            status = "wrong_resident"
        elif issues[0].startswith("foreign"):
            status = "wrong_foreign"
        else:
            status = "wrong_currency"

        results.append(
            RowResult(
                golden=g,
                status=status,
                issues=issues,
                matched=True,
                flagged=bool(item.needs_review),
                parser_resident=res,
                parser_nonresident=non,
                parser_name=item.service_name_raw,
            )
        )

    return FileScore(
        source_file=golden.source_file,
        partner=golden.partner,
        total=len(golden.rows),
        rows=results,
    )
