#!/usr/bin/env python3
"""Autotest: verify the parser populated the database correctly (ТЗ Кейс 2 — MedArchive).

This is a *black-box* acceptance test for the ingestion pipeline. It does NOT re-implement
parsing; it inspects what the parser wrote to Postgres and asserts the structural and
data-quality contract from the ТЗ (sections 3 — schema, 4.3/4.4 — normalization &
validation, 4.5 — GET API). GET endpoints are exercised in-process via FastAPI's
TestClient against the same database, so no running server is required.

Checks are split into two tiers:
  * HARD — parser-correctness invariants. Any failure exits non-zero (the parser is wrong).
  * KPI  — ТЗ acceptance targets (e.g. ≥70% auto-normalization) that depend on the
           supplied catalogue. Reported and warned-on, but non-fatal unless --strict.

Usage:
    uv run python tests/test_parser_db.py                  # verify the current DB
    uv run python tests/test_parser_db.py --strict         # KPI shortfalls also fail
    uv run python tests/test_parser_db.py --min-match-rate 0.5
    uv run python tests/test_parser_db.py --ingest "Хакатон"   # ingest a dir/zip first, then verify

Exit code: 0 = all hard checks passed (KPI may warn); 1 = a hard check failed
(or a KPI check failed under --strict); 2 = the database is empty / nothing to verify.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

# Allow running as a plain script (`python tests/test_parser_db.py`) from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, inspect, or_, select  # noqa: E402

from app.config import FX_TO_KZT, MATCH_AUTO_THRESHOLD  # noqa: E402
from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.models import (  # noqa: E402
    CURRENCIES,
    FILE_FORMATS,
    PARSE_STATUSES,
    Partner,
    PriceDocument,
    PriceItem,
    Service,
)
from app.validation import IMPLAUSIBLE_PRICE  # noqa: E402

# The reference dataset: the ТЗ archive is exactly these eight partner clinics.
EXPECTED_CLINICS = {f"Клиника {i}" for i in range(1, 9)}
# Proof that the multi-format extractor works: each of these must produce ≥1 document.
REQUIRED_FORMATS = {"pdf", "docx", "xlsx", "xls"}
# ТЗ section-3 required columns per table (a subset is enough to prove the schema).
REQUIRED_COLUMNS = {
    "partners": {
        "partner_id",
        "name",
        "city",
        "address",
        "bin",
        "contact_email",
        "contact_phone",
        "is_active",
        "created_at",
        "updated_at",
    },
    "price_documents": {
        "doc_id",
        "partner_id",
        "file_name",
        "file_format",
        "effective_date",
        "parsed_at",
        "parse_status",
        "parse_log",
        "raw_content",
    },
    "price_items": {
        "item_id",
        "doc_id",
        "partner_id",
        "service_name_raw",
        "name_norm",
        "service_code_source",
        "service_id",
        "price_resident_kzt",
        "price_nonresident_kzt",
        "price_original",
        "currency_original",
        "match_method",
        "match_confidence",
        "is_verified",
        "verification_note",
        "needs_review",
        "review_reason",
        "section",
        "unit",
        "effective_date",
        "is_active",
        "created_at",
    },
    "services": {
        "service_id",
        "service_name",
        "synonyms",
        "category",
        "icd_code",
        "is_active",
    },
}

# ANSI colours (suppressed when stdout is not a TTY).
_TTY = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


class Checker:
    """Minimal PASS/FAIL/WARN harness — keeps the test dependency-free (no pytest)."""

    def __init__(self) -> None:
        self.hard_failures = 0
        self.kpi_failures = 0
        self.passed = 0
        self.warned = 0
        self._section = ""

    def section(self, title: str) -> None:
        self._section = title
        print(f"\n{_c('1;36', '== ' + title + ' ==')}")

    def check(
        self, ok: bool, name: str, detail: str = "", *, tier: str = "hard"
    ) -> bool:
        if ok:
            self.passed += 1
            print(f"  {_c('32', 'PASS')}  {name}")
        elif tier == "kpi":
            self.kpi_failures += 1
            print(f"  {_c('33', 'WARN')}  {name}" + (f"  — {detail}" if detail else ""))
        else:
            self.hard_failures += 1
            print(f"  {_c('31', 'FAIL')}  {name}" + (f"  — {detail}" if detail else ""))
        return ok

    def info(self, msg: str) -> None:
        print(f"  {_c('2', '·')}  {msg}")

    def summary(self, strict: bool) -> int:
        print(f"\n{_c('1', '─' * 60)}")
        print(
            f"  {_c('32', str(self.passed) + ' passed')}, "
            f"{_c('31', str(self.hard_failures) + ' failed')}, "
            f"{_c('33', str(self.kpi_failures) + ' KPI warnings')}"
        )
        failed = self.hard_failures + (self.kpi_failures if strict else 0)
        if failed:
            print(_c("1;31", "  RESULT: FAIL"))
            return 1
        if self.kpi_failures:
            print(
                _c(
                    "1;33",
                    "  RESULT: PASS (with KPI warnings — see ТЗ acceptance targets)",
                )
            )
        else:
            print(_c("1;32", "  RESULT: PASS"))
        return 0


# --------------------------------------------------------------------------- schema
def check_schema(c: Checker) -> None:
    c.section("Database schema (ТЗ 3)")
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for table, required in REQUIRED_COLUMNS.items():
        if not c.check(table in tables, f"table '{table}' exists"):
            continue
        cols = {col["name"] for col in insp.get_columns(table)}
        missing = required - cols
        c.check(
            not missing,
            f"table '{table}' has all required columns",
            f"missing: {sorted(missing)}",
        )


# --------------------------------------------------------------------------- coverage
def check_source_coverage(c: Checker, s) -> None:
    c.section("Source coverage (8 clinics × 4 formats)")
    names = set(s.scalars(select(Partner.name)).all())
    missing_clinics = EXPECTED_CLINICS - names
    c.check(
        not missing_clinics,
        "all 8 partner clinics present (Клиника 1–8)",
        f"missing: {sorted(missing_clinics)}",
    )
    c.info(f"partners in DB: {len(names)}")

    formats = set(s.scalars(select(PriceDocument.file_format)).all())
    missing_fmt = REQUIRED_FORMATS - formats
    c.check(
        not missing_fmt,
        "every required file format parsed (pdf/docx/xlsx/xls)",
        f"missing: {sorted(missing_fmt)}",
    )
    c.info(f"formats seen: {sorted(formats)}")

    docs = s.scalars(select(PriceDocument)).all()
    c.check(
        len(docs) >= len(EXPECTED_CLINICS),
        "at least one document per clinic",
        f"only {len(docs)} documents",
    )

    bad_status = [d.file_name for d in docs if d.parse_status not in PARSE_STATUSES]
    c.check(not bad_status, "all documents have a valid parse_status", f"{bad_status}")

    no_ts = [d.file_name for d in docs if d.parsed_at is None]
    c.check(not no_ts, "all documents have parsed_at set", f"{no_ts}")

    # A document with zero extracted rows must be marked 'error', never silently 'done'.
    empty_ok = True
    for d in docs:
        n = s.scalar(
            select(func.count())
            .select_from(PriceItem)
            .where(PriceItem.doc_id == d.doc_id)
        )
        if n == 0 and d.parse_status != "error":
            empty_ok = False
            c.info(
                f"document with 0 rows not marked error: {d.file_name} ({d.parse_status})"
            )
    c.check(empty_ok, "documents with no rows are flagged 'error' (ТЗ 4.4)")

    future = [
        d.file_name
        for d in docs
        if d.effective_date and d.effective_date > date.today()
    ]
    c.check(not future, "no document effective_date is in the future", f"{future}")


# --------------------------------------------------------------------------- data quality
def _count(s, *where) -> int:
    return s.scalar(select(func.count()).select_from(PriceItem).where(*where)) or 0


def check_data_quality(c: Checker, s) -> None:
    c.section("PriceItem data quality (ТЗ 4.3 / 4.4)")
    total = _count(s)
    if not c.check(total > 0, "price_items table is populated", "no rows"):
        return
    c.info(f"price_items: {total} ({_count(s, PriceItem.is_active.is_(True))} active)")

    # Every row keeps the verbatim source name and a normalized search key (ТЗ 4.3).
    c.check(
        _count(
            s,
            or_(PriceItem.service_name_raw.is_(None), PriceItem.service_name_raw == ""),
        )
        == 0,
        "every item stores a non-empty service_name_raw",
    )
    c.check(
        _count(s, PriceItem.name_norm.is_(None)) == 0,
        "every item has a normalized name_norm",
    )

    # Enum integrity.
    bad_cur = set(s.scalars(select(PriceItem.currency_original).distinct())) - set(
        CURRENCIES
    )
    c.check(not bad_cur, "currency_original is always one of KZT/USD/RUB", f"{bad_cur}")
    bad_fmt = set(s.scalars(select(PriceDocument.file_format).distinct())) - set(
        FILE_FORMATS
    )
    c.check(not bad_fmt, "file_format always within the allowed enum", f"{bad_fmt}")

    # Prices: no OCR-concatenation garbage survived as a real value (ТЗ 4.4).
    c.check(
        _count(s, PriceItem.price_resident_kzt > IMPLAUSIBLE_PRICE) == 0,
        f"no resident price exceeds the implausible ceiling ({IMPLAUSIBLE_PRICE:,})",
    )

    # Missing / non-positive price MUST be routed to review, never accepted silently.
    c.check(
        _count(
            s,
            or_(
                PriceItem.price_resident_kzt.is_(None),
                PriceItem.price_resident_kzt <= 0,
            ),
            PriceItem.needs_review.is_(False),
        )
        == 0,
        "missing/non-positive prices are flagged needs_review",
    )

    # nonresident ≥ resident, else review (ТЗ 4.4).
    c.check(
        _count(
            s,
            PriceItem.price_nonresident_kzt.isnot(None),
            PriceItem.price_resident_kzt.isnot(None),
            PriceItem.price_nonresident_kzt < PriceItem.price_resident_kzt,
            PriceItem.needs_review.is_(False),
        )
        == 0,
        "nonresident < resident anomalies are flagged needs_review",
    )

    # Review bookkeeping is consistent: a flag without a reason is a parser bug.
    c.check(
        _count(
            s,
            PriceItem.needs_review.is_(True),
            or_(PriceItem.review_reason.is_(None), PriceItem.review_reason == ""),
        )
        == 0,
        "every needs_review item carries a review_reason",
    )

    # effective_date never in the future (ТЗ 4.4).
    c.check(
        _count(s, PriceItem.effective_date > date.today()) == 0,
        "no item effective_date is in the future",
    )

    # Currency conversion: KZT price equals original × FX rate (ТЗ 4.4).
    mismatches = 0
    rows = s.execute(
        select(
            PriceItem.currency_original,
            PriceItem.price_original,
            PriceItem.price_resident_kzt,
        ).where(
            PriceItem.currency_original != "KZT",
            PriceItem.price_original.isnot(None),
            PriceItem.price_resident_kzt.isnot(None),
        )
    ).all()
    for cur, orig, kzt in rows:
        expected = round(float(orig) * FX_TO_KZT.get(cur, 1.0), 2)
        if abs(expected - float(kzt)) > 0.01:
            mismatches += 1
    c.check(
        mismatches == 0,
        "non-KZT prices are converted to KZT correctly",
        f"{mismatches} of {len(rows)} converted rows wrong",
    )
    c.info(f"non-KZT converted rows checked: {len(rows)}")

    # Match metadata consistency (ТЗ 4.3).
    c.check(
        _count(s, PriceItem.service_id.isnot(None), PriceItem.match_method.is_(None))
        == 0,
        "matched items always record a match_method",
    )
    c.check(
        _count(
            s,
            PriceItem.service_id.isnot(None),
            PriceItem.match_method != "manual",
            PriceItem.match_confidence < MATCH_AUTO_THRESHOLD,
        )
        == 0,
        f"auto-matched items meet the confidence threshold (≥{MATCH_AUTO_THRESHOLD})",
    )
    c.check(
        _count(
            s,
            PriceItem.match_confidence.isnot(None),
            or_(PriceItem.match_confidence < 0, PriceItem.match_confidence > 1),
        )
        == 0,
        "match_confidence is within [0, 1]",
    )

    # Referential integrity: no dangling service_id.
    valid = set(s.scalars(select(Service.service_id)))
    used = set(
        s.scalars(
            select(PriceItem.service_id)
            .where(PriceItem.service_id.isnot(None))
            .distinct()
        )
    )
    c.check(
        used <= valid,
        "every service_id references an existing Service",
        f"{len(used - valid)} orphans",
    )


# --------------------------------------------------------------------------- versioning
def check_versioning(c: Checker, s) -> None:
    c.section("Price versioning & history (ТЗ 4.4)")
    inactive = _count(s, PriceItem.is_active.is_(False))
    c.check(
        inactive > 0,
        "superseded price history is retained (is_active=false rows exist)",
        "no inactive rows — versioning may not be working",
    )
    c.info(f"inactive (archived) items: {inactive}")

    # At most one active price per (partner, version-key=code|name). Duplicates mean a
    # supersede step was missed.
    from collections import Counter

    keyc: Counter = Counter()
    for pid, code, nn in s.execute(
        select(
            PriceItem.partner_id, PriceItem.service_code_source, PriceItem.name_norm
        ).where(PriceItem.is_active.is_(True))
    ).all():
        keyc[(pid, (code or nn or "").lower())] += 1
    dup = sum(1 for v in keyc.values() if v > 1)
    c.check(
        dup == 0,
        "no duplicate active price for the same (partner, service) key",
        f"{dup} duplicate active groups",
    )


# --------------------------------------------------------------------------- KPI
def check_kpi(c: Checker, s, min_rate: float) -> None:
    c.section("ТЗ acceptance KPIs (soft — catalogue-dependent)")
    active = _count(s, PriceItem.is_active.is_(True))
    matched = _count(s, PriceItem.is_active.is_(True), PriceItem.service_id.isnot(None))
    rate = matched / active if active else 0.0
    c.info(
        f"auto-match rate: {matched}/{active} = {rate * 100:.1f}%  (ТЗ target ≥{min_rate * 100:.0f}%)"
    )
    c.check(
        rate >= min_rate,
        f"auto-normalization rate meets ТЗ target (≥{min_rate * 100:.0f}%)",
        f"only {rate * 100:.1f}% — load the organizers' catalogue to raise this",
        tier="kpi",
    )

    services = s.scalar(select(func.count()).select_from(Service)) or 0
    c.check(
        services > 0,
        "a service catalogue is loaded (ТЗ 2.2 / 7)",
        "no services — run load-catalogue or bootstrap-catalogue",
        tier="kpi",
    )
    c.info(f"catalogue services: {services}")


# --------------------------------------------------------------------------- GET API
def _pick_search_term(s) -> str | None:
    """A word guaranteed to exist in some service_name_raw, for an end-to-end search hit."""
    raw = s.scalar(
        select(PriceItem.service_name_raw)
        .where(
            PriceItem.is_active.is_(True), func.length(PriceItem.service_name_raw) > 6
        )
        .limit(1)
    )
    if not raw:
        return None
    for word in re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", raw):
        return word  # verbatim substring → robust to collation/case-folding differences
    return None


def check_get_api(c: Checker, s) -> None:
    c.section("GET API endpoints (ТЗ 4.5)")
    from fastapi.testclient import TestClient

    from app.api import app

    db_docs = s.scalar(select(func.count()).select_from(PriceDocument)) or 0
    db_partners = s.scalar(select(func.count()).select_from(Partner)) or 0
    db_items = s.scalar(select(func.count()).select_from(PriceItem)) or 0

    with TestClient(app) as client:
        # /stats — must agree with the database (no drift between API and storage).
        r = client.get("/stats")
        if c.check(r.status_code == 200, "GET /stats → 200", f"got {r.status_code}"):
            st = r.json()
            c.check(
                st.get("documents") == db_docs,
                "/stats documents matches DB",
                f"{st.get('documents')} vs {db_docs}",
            )
            c.check(
                st.get("partners") == db_partners,
                "/stats partners matches DB",
                f"{st.get('partners')} vs {db_partners}",
            )
            c.check(
                st.get("price_items") == db_items,
                "/stats price_items matches DB",
                f"{st.get('price_items')} vs {db_items}",
            )
            c.check("match_rate_pct" in st, "/stats reports match_rate_pct")

        # /documents
        r = client.get("/documents", params={"limit": 2000})
        if c.check(
            r.status_code == 200, "GET /documents → 200", f"got {r.status_code}"
        ):
            body = r.json()
            c.check(
                len(body) == db_docs,
                "/documents returns every document",
                f"{len(body)} vs {db_docs}",
            )
            c.check(
                all("parse_status" in d and "file_format" in d for d in body),
                "/documents entries expose parse_status & file_format",
            )

        # /partners (+ filter params must not error)
        r = client.get("/partners", params={"limit": 1000})
        if c.check(r.status_code == 200, "GET /partners → 200", f"got {r.status_code}"):
            body = r.json()
            c.check(
                len(body) == db_partners,
                "/partners returns every partner",
                f"{len(body)} vs {db_partners}",
            )
        c.check(
            client.get("/partners", params={"city": "Алматы"}).status_code == 200,
            "GET /partners?city= filter works",
        )
        c.check(
            client.get("/partners", params={"is_active": True}).status_code == 200,
            "GET /partners?is_active= filter works",
        )

        # /partners/{id}/services — a clinic's full price list, with prices.
        pid = s.scalar(select(Partner.partner_id).order_by(Partner.name).limit(1))
        r = client.get(f"/partners/{pid}/services", params={"limit": 10000})
        if c.check(
            r.status_code == 200,
            "GET /partners/{id}/services → 200",
            f"got {r.status_code}",
        ):
            body = r.json()
            c.check(
                len(body) > 0, "/partners/{id}/services returns this clinic's items"
            )
            c.check(
                all(it["partner_id"] == str(pid) for it in body),
                "/partners/{id}/services items all belong to the partner",
            )
            c.check(
                all("price_resident_kzt" in it for it in body),
                "/partners/{id}/services items expose price fields",
            )
        c.check(
            client.get(f"/partners/{pid}/services").status_code == 200,
            "GET /partners/{id}/services (default limit) works",
        )

        # /services (+ category filter)
        r = client.get("/services", params={"limit": 1000})
        if c.check(r.status_code == 200, "GET /services → 200", f"got {r.status_code}"):
            svcs = r.json()
            c.check(
                isinstance(svcs, list) and len(svcs) <= 1000, "/services respects limit"
            )
            cat = next((sv["category"] for sv in svcs if sv.get("category")), None)
            if cat:
                fr = client.get("/services", params={"category": cat, "limit": 1000})
                ok = fr.status_code == 200 and all(
                    sv["category"] == cat for sv in fr.json()
                )
                c.check(ok, "GET /services?category= returns only that category")

        # /services/{id}/partners — must carry BOTH resident & nonresident prices (ТЗ 4.5).
        sid = s.scalar(
            select(PriceItem.service_id)
            .where(PriceItem.service_id.isnot(None), PriceItem.is_active.is_(True))
            .limit(1)
        )
        if sid is not None:
            r = client.get(f"/services/{sid}/partners")
            if c.check(
                r.status_code == 200,
                "GET /services/{id}/partners → 200",
                f"got {r.status_code}",
            ):
                body = r.json()
                c.check(len(body) > 0, "/services/{id}/partners lists offering clinics")
                c.check(
                    all(
                        "price_resident_kzt" in e and "price_nonresident_kzt" in e
                        for e in body
                    ),
                    "/services/{id}/partners includes resident & nonresident prices",
                )
                c.check(
                    all("partner" in e for e in body),
                    "/services/{id}/partners embeds the partner object",
                )
        else:
            c.info(
                "no matched service available — skipping /services/{id}/partners body check"
            )

        # /search — full text across partners + services + raw items.
        term = _pick_search_term(s)
        if term:
            r = client.get("/search", params={"q": term})
            if c.check(
                r.status_code == 200,
                f"GET /search?q={term!r} → 200",
                f"got {r.status_code}",
            ):
                body = r.json()
                c.check(
                    {"partners", "services", "price_items"} <= set(body),
                    "/search returns partners + services + price_items",
                )
                c.check(
                    len(body["price_items"]) > 0,
                    "/search finds the known term among price items",
                )
                c.check(
                    all("partner_name" in it for it in body["price_items"]),
                    "/search price items are enriched with partner_name",
                )

        # /unmatched — operator queue: only un-normalized positions.
        r = client.get("/unmatched", params={"limit": 2000})
        if c.check(
            r.status_code == 200, "GET /unmatched → 200", f"got {r.status_code}"
        ):
            c.check(
                all(it["service_id"] is None for it in r.json()),
                "/unmatched returns only items without a service_id",
            )

        # /review — validation queue.
        r = client.get("/review", params={"limit": 2000})
        if c.check(r.status_code == 200, "GET /review → 200", f"got {r.status_code}"):
            body = r.json()
            c.check(
                all(it["needs_review"] and not it["is_verified"] for it in body),
                "/review returns only unverified, flagged items",
            )

        # /ocr/health — optional OCR subsystem.
        r = client.get("/ocr/health")
        if c.check(
            r.status_code == 200, "GET /ocr/health → 200", f"got {r.status_code}"
        ):
            c.check("available" in r.json(), "/ocr/health reports availability")


# --------------------------------------------------------------------------- driver
def _ingest(path: Path, c: Checker) -> None:
    """Optional: run the real pipeline over a folder/zip before verifying (true E2E)."""
    from app.cli import _iter_files
    from app.pipeline import ingest_file

    files = list(_iter_files(path))
    c.info(f"ingesting {len(files)} file(s) from {path} …")
    with SessionLocal() as s:
        for f in files:
            try:
                rep = ingest_file(s, f)
                tag = "dup" if rep.skipped_duplicate else rep.status
                c.info(f"  {f.name[:48]:50} [{tag}] written={rep.rows_written}")
            except Exception as e:  # noqa: BLE001 — one bad file must not abort the batch
                s.rollback()
                c.info(f"  {f.name}: {type(e).__name__}: {e}")


def run_all(args: argparse.Namespace) -> Checker:
    c = Checker()
    init_db()  # schema must exist before we inspect it

    if args.ingest:
        target = Path(args.ingest)
        if not target.is_absolute():
            target = Path(__file__).resolve().parent.parent / target
        if target.exists():
            _ingest(target, c)
        else:
            c.info(f"--ingest path not found, skipping: {target}")

    check_schema(c)
    with SessionLocal() as s:
        total = s.scalar(select(func.count()).select_from(PriceItem)) or 0
        if total == 0:
            c.section("Empty database")
            c.info("No price_items found. Ingest the archive first, e.g.:")
            c.info('  uv run python -m app.cli ingest "Хакатон"')
            c.info('or re-run this test with --ingest "Хакатон".')
            c._empty = True  # type: ignore[attr-defined]
            return c
        check_source_coverage(c, s)
        check_data_quality(c, s)
        check_versioning(c, s)
        check_kpi(c, s, args.min_match_rate)
        check_get_api(c, s)
    return c


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Verify the parser populated the DB correctly (ТЗ Кейс 2)."
    )
    p.add_argument(
        "--strict", action="store_true", help="treat KPI shortfalls as failures"
    )
    p.add_argument(
        "--min-match-rate",
        type=float,
        default=0.70,
        help="ТЗ auto-normalization target (default 0.70 = 70%%)",
    )
    p.add_argument(
        "--ingest",
        metavar="PATH",
        help="ingest a folder/zip through the real pipeline before verifying",
    )
    args = p.parse_args(argv)

    c = run_all(args)
    if getattr(c, "_empty", False):
        return 2
    return c.summary(strict=args.strict)


# pytest entry point (if pytest is ever installed); harmless otherwise.
def test_parser_database() -> None:
    c = run_all(argparse.Namespace(strict=False, min_match_rate=0.70, ingest=None))
    assert not getattr(c, "_empty", False), (
        "database is empty — ingest the archive first"
    )
    assert c.hard_failures == 0, (
        f"{c.hard_failures} hard parser-correctness check(s) failed"
    )


if __name__ == "__main__":
    raise SystemExit(main())
