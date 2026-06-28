"""Render golden-standard files as readable HTML tables for manual review.

Read-only: this is a review aid, not an editor — edit the JSON under tests/golden/
directly (schema in tests/eval/golden.py: GoldenRow). One section per file with all
fields shown, so ground truth can be eyeballed against the source documents.

    uv run python -m tests.eval.golden_review                 # all goldens
    uv run python -m tests.eval.golden_review --only klinika_1_2026 klinika_5
    uv run python -m tests.eval.golden_review --glob 'klinika_[12345]*'
"""

from __future__ import annotations

import argparse
import html
from pathlib import Path

from tests.eval.golden import GOLDEN_DIR, GoldenFile, load_golden

ROOT = Path(__file__).resolve().parents[2]


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _price(v) -> str:
    return f"{v:,.0f}".replace(",", " ") if isinstance(v, (int, float)) else "—"


def _tiers(d) -> str:
    if not d:
        return "—"
    return ", ".join(f"{_e(k)}={_price(v)}" for k, v in d.items())


_CSS = """
*{box-sizing:border-box}
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#1f2328;margin:0;background:#f6f8fa}
.wrap{max-width:1180px;margin:0 auto;padding:30px 24px 64px}
a{color:#0969da;text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:21px;margin:0 0 6px}h2{font-size:16px;margin:30px 0 4px}
.meta{color:#57606a;font-size:12.5px;margin:0 0 8px}
.meta code{font-family:ui-monospace,Menlo,Consolas,monospace;background:#eaeef2;padding:1px 6px;border-radius:5px}
.toc{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0 8px}
.toc a{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:6px 11px;font-size:13px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d0d7de;border-radius:10px;overflow:hidden;margin-bottom:6px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#57606a;
   background:#f6f8fa;padding:8px 10px;border-bottom:1px solid #d0d7de}
td{padding:7px 10px;border-bottom:1px solid #eaeef2;vertical-align:top}
tr:last-child td{border-bottom:none}
.num{color:#8c959f;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;text-align:right;width:32px}
.svc{font-weight:500;max-width:360px}
.code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:#57606a}
.p{font-variant-numeric:tabular-nums;text-align:right;font-weight:600;white-space:nowrap;width:80px}
.sec{color:#57606a;font-size:12.5px}
.note{color:#7a8290;font-size:12px}
.rev{color:#9a6700;font-weight:600;font-size:12px}
.foot{color:#8c959f;font-size:12px;margin-top:30px}
"""


def render(files: list[tuple[str, GoldenFile]]) -> str:
    toc = "".join(
        f'<a href="#{_e(stem)}">{_e(stem)} <b>({len(g.rows)})</b></a>'
        for stem, g in files
    )
    sections = []
    for stem, g in files:
        rows = []
        for i, r in enumerate(g.rows, 1):
            rev = '<span class="rev">expect review</span>' if r.expect_review else ""
            note = (
                f'<div class="note">{_e(r.note)} {rev}</div>' if (r.note or rev) else ""
            )
            rows.append(
                f'<tr><td class="num">{i}</td>'
                f'<td class="svc">{_e(r.name)}{note}</td>'
                f'<td class="code">{_e(r.code) or "—"}</td>'
                f'<td class="sec">{_e(r.unit) or "—"}</td>'
                f'<td class="sec">{_e(r.section) or "—"}</td>'
                f'<td class="p">{_price(r.price_resident_kzt)}</td>'
                f'<td class="p">{_price(r.price_nonresident_kzt)}</td>'
                f'<td class="code">{_tiers(r.price_extra_tiers)}</td>'
                f'<td class="code">{_e(r.currency_original)}</td></tr>'
            )
        sections.append(
            f'<h2 id="{_e(stem)}">{_e(g.partner)} — <code>{_e(stem)}</code></h2>'
            f'<p class="meta">source: <code>{_e(g.source_file)}</code> &nbsp;·&nbsp; '
            f"extracted_by: {_e(g.extracted_by)} &nbsp;·&nbsp; "
            f"sampled: <b>{_e(g.sampled)}</b> &nbsp;·&nbsp; {len(g.rows)} rows &nbsp;·&nbsp; "
            f"edit: <code>tests/golden/{_e(stem)}.json</code></p>"
            f"<table><thead><tr><th></th><th>Service name</th><th>Code</th><th>Unit</th>"
            f"<th>Section</th><th>Resident</th><th>Non&#8209;res.</th><th>Extra tiers</th>"
            f"<th>Cur</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
        )
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Golden standard — review</title><style>{_CSS}</style></head><body><div class="wrap">
<h1>Golden standard — manual review</h1>
<p class="meta">{len(files)} file(s). Read-only view; edit the JSON under
<code>tests/golden/</code> (schema: <code>tests/eval/golden.py → GoldenRow</code>).
Resident = первичный/local slot · Non-res. = повторный/foreigner slot.</p>
<div class="toc">{toc}</div>
{"".join(sections)}
<p class="foot">After editing, re-run the OCR preview batch:
<code>uv run python -m tests.eval.scan_preview --batch --out-dir ocr_preview</code></p>
</div></body></html>"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", help="golden stems (e.g. klinika_5)")
    ap.add_argument("--glob", default="*", help="glob over tests/golden (default all)")
    ap.add_argument("--out", default="golden_review.html")
    a = ap.parse_args(argv)

    paths = sorted(GOLDEN_DIR.glob(f"{a.glob}.json"))
    if a.only:
        want = {n.removesuffix(".json") for n in a.only}
        paths = [p for p in paths if p.stem in want]
    if not paths:
        print(f"no golden files matched in {GOLDEN_DIR}")
        return 2
    files = [(p.stem, load_golden(p)) for p in paths]
    Path(a.out).write_text(render(files), encoding="utf-8")
    print(f"wrote {a.out}  ({len(files)} files: {', '.join(s for s, _ in files)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
