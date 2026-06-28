"""Merge per-page vision-OCR row files into one complete golden per clinic.

Each transcription subagent wrote a JSON array of row dicts (fields matching GoldenRow,
plus a `page` provenance int) for a disjoint page range, under that clinic's rows dir.
This concatenates them in page order, forward-fills `section` across page boundaries
(a page that starts mid-section emits section=null), light-validates, and writes
tests/golden/<stem>.json (sampled=false — these are full transcriptions).

    uv run python -m tests.eval.build_golden --stem klinika_1_2026
    uv run python -m tests.eval.build_golden --all          # every clinic with rows present
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCR = Path(
    "/tmp/claude-1000/-home-altairzhambyl-projects-medhack2/"
    "7e28338a-b6ca-4825-9dfd-b368d0fd9bc5/scratchpad"
)
GOLDEN_DIR = ROOT / "tests" / "golden"
CURRENCIES = {"KZT", "USD", "RUB"}
FIELDS = (
    "name",
    "code",
    "unit",
    "section",
    "price_resident_kzt",
    "price_nonresident_kzt",
    "price_extra_tiers",
    "currency_original",
    "note",
    "page",
)

# stem -> (rows_dir, source_file, partner)
CONFIG = {
    "klinika_1_2026": (SCR / "rows", "Клиника 1 2026.pdf", "Клиника 1"),
    "klinika_1_2024": (
        SCR / "klinika_1_2024/rows",
        "Клиника 1 прайс 2024.docx",
        "Клиника 1",
    ),
    "klinika_2_2025": (
        SCR / "klinika_2_2025/rows",
        "Клиника 2 прайс 2025 год.PDF",
        "Клиника 2",
    ),
    "klinika_2_2026": (
        SCR / "klinika_2_2026/rows",
        "Клиника 2 прайс 2026.pdf",
        "Клиника 2",
    ),
    "klinika_3": (SCR / "klinika_3/rows", "Клиника 3 прайс 2026.PDF", "Клиника 3"),
    "klinika_4": (SCR / "klinika_4/rows", "Клиника 4 прайс 2026.pdf", "Клиника 4"),
    "klinika_5": (SCR / "klinika_5/rows", "Клиника 5 прайс 2025.pdf", "Клиника 5"),
}


def _clean_int(v):
    if v is None or isinstance(v, (int, float)):
        return v
    s = str(v).replace(" ", "").replace(" ", "").replace("тг", "").replace("тенге", "")
    s = s.replace(",", "").strip()
    return int(s) if s.lstrip("-").isdigit() else None


def build(stem: str) -> dict:
    rows_dir, source_file, partner = CONFIG[stem]
    files = sorted(rows_dir.glob("rows_p*.json"))
    if not files:
        return {"stem": stem, "error": f"no row files in {rows_dir}"}

    rows: list[dict] = []
    per_file = {}
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        per_file[f.name] = len(data)
        rows.extend(data)

    # Page-order is already filename order (zero-padded, disjoint ranges); stable-sort by page
    # to be safe, preserving within-page transcription order.
    rows.sort(key=lambda r: r.get("page") or 0)

    # forward-fill section across page boundaries
    last_section = None
    for r in rows:
        s = r.get("section")
        if s:
            last_section = s
        elif last_section:
            r["section"] = last_section
        # normalize / coerce price fields
        r["price_resident_kzt"] = _clean_int(r.get("price_resident_kzt"))
        r["price_nonresident_kzt"] = _clean_int(r.get("price_nonresident_kzt"))
        tiers = r.get("price_extra_tiers")
        if isinstance(tiers, dict):
            tiers = {k: _clean_int(v) for k, v in tiers.items()}
            r["price_extra_tiers"] = {
                k: v for k, v in tiers.items() if v is not None
            } or None
        r.setdefault("currency_original", "KZT")
        # keep only the known fields
        for k in list(r.keys()):
            if k not in FIELDS:
                r.pop(k)

    doc = {
        "source_file": source_file,
        "partner": partner,
        "extracted_by": "claude-vision",
        "sampled": False,
        "rows": rows,
    }
    out = GOLDEN_DIR / f"{stem}.json"
    out.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # ---- validation report ----
    pages = sorted({r.get("page") for r in rows if r.get("page")})
    missing = (
        [p for p in range(pages[0], pages[-1] + 1) if p not in pages] if pages else []
    )
    noprice = sum(
        1
        for r in rows
        if r.get("price_resident_kzt") is None
        and r.get("price_nonresident_kzt") is None
        and not r.get("price_extra_tiers")
    )
    bad_cur = sorted(
        {
            r["currency_original"]
            for r in rows
            if r.get("currency_original") not in CURRENCIES
        }
    )
    nosection = sum(1 for r in rows if not r.get("section"))
    codes = [r.get("code") for r in rows if r.get("code")]
    return {
        "stem": stem,
        "rows": len(rows),
        "files": len(files),
        "per_file": per_file,
        "pages": f"{pages[0]}–{pages[-1]}" if pages else "—",
        "missing_pages": missing,
        "sections": len({r.get("section") for r in rows if r.get("section")}),
        "rows_no_section": nosection,
        "rows_no_price": noprice,
        "dup_codes": sum(c for c in Counter(codes).values() if c > 1)
        - len({c for c, n in Counter(codes).items() if n > 1})
        if codes
        else 0,
        "bad_currency": bad_cur,
        "out": str(out.relative_to(ROOT)),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stem", help="clinic stem (e.g. klinika_1_2026)")
    ap.add_argument(
        "--all", action="store_true", help="build every clinic that has row files"
    )
    a = ap.parse_args(argv)

    stems = list(CONFIG) if a.all else [a.stem] if a.stem else []
    if not stems:
        ap.error("pass --stem <name> or --all")
    for stem in stems:
        if stem not in CONFIG:
            print(f"  ! unknown stem: {stem}")
            continue
        rep = build(stem)
        if rep.get("error"):
            print(f"  · {stem}: {rep['error']}")
            continue
        print(
            f"✓ {stem}: {rep['rows']} rows from {rep['files']} files · pages {rep['pages']} "
            f"(missing {rep['missing_pages'] or 'none'}) · {rep['sections']} sections · "
            f"no-price {rep['rows_no_price']} · no-section {rep['rows_no_section']} · "
            f"dup-codes {rep['dup_codes']} · bad-cur {rep['bad_currency'] or 'none'}\n"
            f"   → {rep['out']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
