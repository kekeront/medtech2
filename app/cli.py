"""CLI: initialise the schema and ingest files / folders / ZIP archives.

Usage:
  python -m app.cli init
  python -m app.cli ingest <path-to-file-or-folder-or-zip> [--force]
  python -m app.cli stats
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path

from sqlalchemy import func, select

from .db import SessionLocal, init_db
from .models import PriceDocument, PriceItem, Service
from .pipeline import ingest_file

SUPPORTED = {".pdf", ".docx", ".xlsx", ".xls"}


def _iter_files(target: Path):
    if target.is_file() and target.suffix.lower() == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="medarchive_zip_"))
        with zipfile.ZipFile(target) as z:
            z.extractall(tmp)
        yield from _iter_files(tmp)
    elif target.is_dir():
        for p in sorted(target.rglob("*")):
            if p.is_file() and p.suffix.lower() in SUPPORTED:
                yield p
    elif target.suffix.lower() in SUPPORTED:
        yield target


def cmd_ingest(args) -> int:
    init_db()
    target = Path(args.path)
    if not target.exists():
        print(f"path not found: {target}", file=sys.stderr)
        return 1

    files = list(_iter_files(target))
    if not files:
        print("no supported files found (.pdf/.docx/.xlsx/.xls)", file=sys.stderr)
        return 1

    totals = {"written": 0, "review": 0, "matched": 0, "dropped": 0, "docs": 0}
    with SessionLocal() as session:
        for f in files:
            try:
                rep = ingest_file(session, f)
            except Exception as e:  # noqa: BLE001 - one bad file must not abort the batch
                session.rollback()
                print(f"  ✗ {f.name}: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            tag = "dup" if rep.skipped_duplicate else rep.status
            print(
                f"  {f.name[:42]:44} [{tag:12}] "
                f"parsed={rep.rows_parsed:5} written={rep.rows_written:5} "
                f"review={rep.rows_needs_review:5} dropped={rep.rows_dropped:5} "
                f"matched={rep.rows_matched:5} -> {rep.partner}"
            )
            totals["docs"] += 1
            totals["written"] += rep.rows_written
            totals["review"] += rep.rows_needs_review
            totals["matched"] += rep.rows_matched
            totals["dropped"] += rep.rows_dropped

    print(
        f"\nDONE: {totals['docs']} docs, {totals['written']} items written, "
        f"{totals['review']} need review, {totals['matched']} auto-matched, "
        f"{totals['dropped']} dropped."
    )
    return 0


def cmd_init(_args) -> int:
    init_db()
    print("schema created / verified.")
    return 0


def cmd_stats(_args) -> int:
    with SessionLocal() as session:
        docs = session.scalar(select(func.count()).select_from(PriceDocument)) or 0
        items = session.scalar(select(func.count()).select_from(PriceItem)) or 0
        review = (
            session.scalar(
                select(func.count())
                .select_from(PriceItem)
                .where(PriceItem.needs_review.is_(True))
            )
            or 0
        )
        matched = (
            session.scalar(
                select(func.count())
                .select_from(PriceItem)
                .where(PriceItem.service_id.isnot(None))
            )
            or 0
        )
        services = session.scalar(select(func.count()).select_from(Service)) or 0
    pct = round(matched / items * 100, 1) if items else 0.0
    print(f"documents: {docs}")
    print(f"price items: {items}  (active history retained)")
    print(f"needs review: {review}")
    print(f"catalogue services: {services}")
    print(f"auto-matched: {matched} ({pct}%)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="medarchive")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(func=cmd_init)
    pi = sub.add_parser("ingest")
    pi.add_argument("path")
    pi.add_argument(
        "--force",
        action="store_true",
        help="re-ingest even if the file was seen before",
    )
    pi.set_defaults(func=cmd_ingest)
    sub.add_parser("stats").set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
