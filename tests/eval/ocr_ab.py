"""A/B OCR engines on one price-list PDF, scored against the golden reference.

Runs each engine over the SAME pages (apples-to-apples) and reports, per engine:
how many golden rows it reproduced faithfully (normalized name match AND resident price
match), how many rows it produced, and wall-clock time. Both Surya and Tesseract run on
CPU here, so the comparison is cool and hardware-neutral.

Usage:
    uv run python -m tests.eval.ocr_ab --file "Хакатон/Клиника 2 прайс 2025 год.PDF" \
        --pages 3 --engines surya tesseract --golden klinika_2_2025
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.normalize import _key  # noqa: E402
from app.tariffs import map_tariffs  # noqa: E402

from .golden import GOLDEN_DIR, load_golden  # noqa: E402

PRICE_TOL = 0.5


def _parse(engine: str, path: str, pages: int):
    if engine == "surya":
        from app.parsers.surya_ocr import parse_pdf_surya

        return parse_pdf_surya(path, max_pages=pages)
    if engine == "tesseract":
        from app.parsers.tesseract_ocr import parse_pdf_tesseract

        return parse_pdf_tesseract(path, max_pages=pages)
    if engine == "geometric":
        from app.parsers.pdf import parse_pdf

        return parse_pdf(path)
    raise SystemExit(f"unknown engine {engine!r}")


def _golden_prices(golden) -> dict[str, set]:
    """normalized name -> the set of golden prices (resident/nonresident, whichever exist).
    A single-column file may put its only price under nonresident, so match against both."""
    out: dict[str, set] = {}
    for r in golden.rows:
        vals = {
            float(v)
            for v in (r.price_resident_kzt, r.price_nonresident_kzt)
            if v is not None
        }
        if vals:
            out[_key(r.name)] = vals
    return out


def _score(result, gmap: dict) -> tuple[int, int]:
    """(faithful rows, total rows) — a row is faithful if its name matches a golden name
    AND any price the engine extracted matches one of that golden row's prices."""
    faithful = 0
    for row in result.rows:
        gvals = gmap.get(_key(row.name))
        if not gvals:
            continue
        res, non, _extra = map_tariffs(row.prices, result.price_labels)
        got = [float(v) for v in (res, non) if v is not None]
        if any(abs(g - e) <= PRICE_TOL for e in got for g in gvals):
            faithful += 1
    return faithful, len(result.rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True)
    p.add_argument("--pages", type=int, default=3)
    p.add_argument("--engines", nargs="+", default=["surya", "tesseract"])
    p.add_argument("--golden", required=True, help="golden stem, e.g. klinika_2_2025")
    p.add_argument(
        "--show", type=int, default=8, help="sample rows to print per engine"
    )
    args = p.parse_args(argv)

    root = Path(__file__).resolve().parents[2]
    path = str(root / args.file) if not Path(args.file).is_absolute() else args.file
    golden = load_golden(GOLDEN_DIR / f"{args.golden}.json")
    gmap = _golden_prices(golden)

    print(f"\nA/B over first {args.pages} page(s) of {Path(path).name}\n" + "─" * 66)
    for engine in args.engines:
        t = time.time()
        try:
            res = _parse(engine, path, args.pages)
        except Exception as exc:  # noqa: BLE001
            print(f"  {engine:10} ERROR: {type(exc).__name__}: {exc}")
            continue
        faithful, total = _score(res, gmap)
        print(
            f"  {engine:10} faithful={faithful:3} / {total:3} rows  "
            f"({time.time() - t:.0f}s)"
        )
        for row in res.rows[: args.show]:
            r, _n, _e = map_tariffs(row.prices, res.price_labels)
            print(f"       {row.name[:46]!r:48} res={r}")
    print("─" * 66)
    print(
        "faithful = name matches golden AND resident price matches (higher is better)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
