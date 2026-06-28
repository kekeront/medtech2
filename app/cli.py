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
from pathlib import Path

from sqlalchemy import func, select

from .archive import (
    SUPPORTED_SUFFIXES,
    collect_documents,
    ensure_parseable,
    extract_archive,
)
from .db import SessionLocal, init_db
from .models import PriceDocument, PriceItem, Service
from .pipeline import ingest_file


def _iter_files(target: Path):
    if target.is_file() and target.suffix.lower() == ".zip":
        tmp = Path(
            tempfile.mkdtemp(prefix="medarchive_zip_")
        )  # persists through ingestion
        extract_archive(target, tmp)
        yield from collect_documents(tmp)
    elif target.is_dir():
        yield from collect_documents(target)
    elif target.suffix.lower() in SUPPORTED_SUFFIXES:
        yield target


def cmd_ingest(args) -> int:
    init_db()
    target = Path(args.path)
    if not target.exists():
        print(f"path not found: {target}", file=sys.stderr)
        return 1

    files = list(_iter_files(target))
    if not files:
        print("no supported files found (.pdf/.docx/.xlsx/.xls/.doc)", file=sys.stderr)
        return 1

    totals = {"written": 0, "review": 0, "matched": 0, "dropped": 0, "docs": 0}
    with SessionLocal() as session:
        for f in files:
            try:
                rep = ingest_file(
                    session,
                    ensure_parseable(f),
                    original_name=f.name,
                    force=args.force,
                    force_ocr=args.ocr,
                )
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


def cmd_load_catalogue(args) -> int:
    init_db()
    from .catalogue import load_catalogue_from_file, rematch

    with SessionLocal() as session:
        created = load_catalogue_from_file(session, args.path)
        stats = rematch(session, only_unmatched=True)
    print(f"catalogue: {created} services imported.")
    print(f"rematch: {stats}")
    return 0


def cmd_bootstrap_catalogue(args) -> int:
    init_db()
    from .catalogue import bootstrap_catalogue, rematch

    with SessionLocal() as session:
        created = bootstrap_catalogue(session, min_count=args.min_count)
        stats = rematch(session, only_unmatched=True)
    print(f"bootstrap: {created} services synthesized from extracted data.")
    print(f"rematch: {stats}")
    return 0


def cmd_rematch(args) -> int:
    init_db()
    from .catalogue import rematch

    with SessionLocal() as session:
        stats = rematch(session, only_unmatched=not args.all)
    print(f"rematch: {stats}")
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
    pi.add_argument(
        "--ocr",
        action="store_true",
        help="force OCR extraction for PDFs (for image scans)",
    )
    pi.set_defaults(func=cmd_ingest)
    sub.add_parser("stats").set_defaults(func=cmd_stats)

    pc = sub.add_parser(
        "load-catalogue", help="import target service catalogue (XLSX/JSON)"
    )
    pc.add_argument("path")
    pc.set_defaults(func=cmd_load_catalogue)

    pb = sub.add_parser(
        "bootstrap-catalogue", help="synthesize a catalogue from extracted data"
    )
    pb.add_argument(
        "--min-count", type=int, default=2, help="min occurrences to form a service"
    )
    pb.set_defaults(func=cmd_bootstrap_catalogue)

    pr = sub.add_parser("rematch", help="re-run catalogue matching over price items")
    pr.add_argument(
        "--all", action="store_true", help="rematch all items, not only unmatched"
    )
    pr.set_defaults(func=cmd_rematch)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
