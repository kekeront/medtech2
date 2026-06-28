"""Backfill the one-pass LLM data-quality cleanup over rows ALREADY in the DB.

Same logic as the ingest-time layer (app/clean_llm.clean_rows), applied to existing
PriceItems so the Проверка page populates without re-uploading. Cleans text in place
(name + recomputed name_norm, unit, section) and (re)flags suspicious rows into
review_reason with the 'LLM:' marker. Numbers/codes are never changed.

Safe to re-run: existing 'LLM:' flags are stripped and recomputed (no duplication), and
verified rows keep their is_verified/needs_review state (text is still cleaned).

Usage (from repo root):
    uv run python scripts/backfill_clean.py                 # dry-run, all active rows
    uv run python scripts/backfill_clean.py --apply         # write changes
    uv run python scripts/backfill_clean.py --partner <id> --limit 200 --apply
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

# Make `app` importable when run by path (uv run python scripts/backfill_clean.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.clean_llm import LLM_FLAG_PREFIX, clean_rows  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import PriceItem  # noqa: E402
from app.normalize import _key, clean_section  # noqa: E402
from app.parsers.base import PriceRow  # noqa: E402


def _to_row(it: PriceItem) -> PriceRow:
    prices = [
        p for p in (it.price_resident_kzt, it.price_nonresident_kzt) if p is not None
    ]
    return PriceRow(
        name=it.service_name_raw,
        code=it.service_code_source,
        unit=it.unit,
        section=it.section,
        resident=it.price_resident_kzt,
        nonresident=it.price_nonresident_kzt,
        prices=prices,
        currency=it.currency_original or "KZT",
        tariffs_resolved=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--partner", help="restrict to one partner_id")
    ap.add_argument("--limit", type=int, help="cap number of rows (testing)")
    ap.add_argument(
        "--apply", action="store_true", help="write changes (default: dry-run)"
    )
    args = ap.parse_args()

    with SessionLocal() as db:
        stmt = select(PriceItem).where(PriceItem.is_active.is_(True))
        if args.partner:
            stmt = stmt.where(PriceItem.partner_id == uuid.UUID(args.partner))
        if args.limit:
            stmt = stmt.limit(args.limit)
        items = db.scalars(stmt).all()
        print(f"loaded {len(items)} active item(s)")
        if not items:
            return

        rows = [_to_row(it) for it in items]
        cleaned, warns = clean_rows(rows, force=True)
        for w in warns:
            print(" ", w)

        n_name = n_text = n_flag = n_rows_flagged = 0
        for it, r in zip(items, cleaned):
            llm_flags = [s for s in r.issues if s.startswith(LLM_FLAG_PREFIX)]
            new_name = (r.name or "").strip() or it.service_name_raw
            if new_name != it.service_name_raw:
                n_name += 1
                if args.apply:
                    it.service_name_raw = new_name
                    it.name_norm = _key(new_name)
            new_unit = (r.unit or None) if r.unit is None else (r.unit.strip() or None)
            new_section = clean_section(r.section)
            if (new_unit != it.unit) or (new_section != it.section):
                n_text += 1
            if args.apply:
                it.unit = new_unit
                it.section = new_section
            if llm_flags:
                n_flag += len(llm_flags)
                n_rows_flagged += 1
            # Don't disturb a human-verified row's review state; just (re)compute for the rest.
            if not it.is_verified and args.apply:
                others = [
                    p.strip()
                    for p in (it.review_reason or "").split(";")
                    if p.strip() and not p.strip().startswith(LLM_FLAG_PREFIX)
                ]
                parts = others + llm_flags
                it.review_reason = "; ".join(parts) or None
                it.needs_review = bool(parts)

        print(
            f"names cleaned: {n_name} | unit/section cleaned: {n_text} | "
            f"rows flagged: {n_rows_flagged} ({n_flag} flags)"
        )
        if args.apply:
            db.commit()
            print("committed.")
        else:
            print("dry-run — re-run with --apply to write.")


if __name__ == "__main__":
    main()
