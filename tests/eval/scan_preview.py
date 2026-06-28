"""Render visual HTML diffs of price-list extraction: golden truth vs OCR output.

Forces the OCR path (app.parsers, file_format="scan_pdf") on a source document and
aligns each golden (ground-truth) row against what the scanner produced, colour-coding
every row:

    green  (ok)      — name AND price both recovered cleanly
    amber  (garbled) — a price digit survived, but the service name was merged into an
                       adjacent row (the pipeline would emit an unusable blob)
    red    (wrong)   — matched by name, but the price disagrees
    grey   (missing) — not recovered from the OCR output at all

Single file:
    uv run python -m tests.eval.scan_preview \
        --golden tests/golden/klinika_5_scan.json --out scan_preview.html

Batch (all clinic 1–5 variations → one HTML each + index.html):
    uv run python -m tests.eval.scan_preview --batch --out-dir ocr_preview

Non-PDF sources (.docx) are converted to PDF with LibreOffice first. OCR depth is
governed by MEDARCHIVE_OCR_MAX_PAGES / MEDARCHIVE_OCR_MAX_SECONDS (set them high for a
full, uncapped pass).
"""

from __future__ import annotations

import argparse
import html
import re
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from app.normalize import _key
from app.parsers.base import PriceRow
from app.parsers.registry import parse_file
from tests.eval.golden import GoldenFile, GoldenRow, load_golden

ROOT = Path(__file__).resolve().parents[2]
ORIGINALS = ROOT / "data" / "originals"
SCRATCH = Path("/tmp/claude-1000/-home-altairzhambyl-projects-medhack2/ocr_preview_tmp")
PRICE_TOL = 0.5

# The clinic 1–5 "variations" — every golden whose source is one of these clinics.
BATCH_GOLDENS = [
    "klinika_1_2024",
    "klinika_1_2026",
    "klinika_2_2025",
    "klinika_2_2026",
    "klinika_3",
    "klinika_4",
    "klinika_5",
    "klinika_5_scan",
]


# ── source resolution ────────────────────────────────────────────────────────
def resolve_source(source_file: str) -> Path | None:
    """Find the physical file in data/originals (stored as '<hash>__<name>')."""
    if not ORIGINALS.exists():
        return None
    sl = source_file.lower()
    for f in ORIGINALS.iterdir():
        n = f.name.lower()
        if n == sl or n.endswith("__" + sl) or n.endswith(sl):
            return f
    return None


def ensure_pdf(src: Path) -> Path:
    """OCR needs a PDF. Convert .docx/.doc to PDF via LibreOffice (cached in SCRATCH)."""
    if src.suffix.lower() == ".pdf":
        return src
    SCRATCH.mkdir(parents=True, exist_ok=True)
    out = SCRATCH / (src.stem + ".pdf")
    if not out.exists():
        # Unique profile dir so concurrent conversions never clash on the lock.
        prof = SCRATCH / f"lo_profile_{src.stem[:8]}"
        subprocess.run(
            [
                "libreoffice",
                f"-env:UserInstallation=file://{prof}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(SCRATCH),
                str(src),
            ],
            check=True,
            capture_output=True,
            timeout=180,
        )
    if not out.exists():
        raise RuntimeError(f"LibreOffice did not produce {out}")
    return out


# ── matching / scoring ───────────────────────────────────────────────────────
def _row_prices(r: PriceRow) -> list[float]:
    if r.tariffs_resolved:
        vals = [r.resident, r.nonresident, *(r.extra_tiers or {}).values()]
        return [float(v) for v in vals if v is not None]
    return [float(v) for v in r.prices if v is not None]


def _has(prices: list[float], target: float | None) -> bool:
    return target is None or any(abs(p - target) <= PRICE_TOL for p in prices)


class Match:
    __slots__ = ("g", "row", "how", "status", "detail")

    def __init__(self, g: GoldenRow):
        self.g = g
        self.row: PriceRow | None = None
        self.how = ""  # "name" | "price" | ""
        self.status = "missing"  # ok | garbled | wrong | missing
        self.detail = ""


def align(
    golden_rows: list[GoldenRow], parsed: list[PriceRow]
) -> tuple[list[Match], set[int]]:
    """Match each golden row to a parser row (name first, then price), then score it."""
    by_name: dict[str, list[int]] = {}
    for i, r in enumerate(parsed):
        by_name.setdefault(_key(r.name), []).append(i)

    used: set[int] = set()
    matches: list[Match] = []
    for g in golden_rows:
        m = Match(g)
        idx: int | None = None
        for i in by_name.get(_key(g.name), []):  # 1) clean normalized-name match
            if i not in used:
                idx, m.how = i, "name"
                break
        if idx is None and g.price_resident_kzt is not None:  # 2) price-only fallback
            for i, r in enumerate(parsed):
                if i not in used and _has(_row_prices(r), g.price_resident_kzt):
                    idx, m.how = i, "price"
                    break
        if idx is None:
            m.status, m.detail = "missing", "not extracted"
        else:
            used.add(idx)
            r = parsed[idx]
            m.row = r
            prices = _row_prices(r)
            if m.how == "price":
                m.status = "garbled"
                m.detail = (
                    "name merged into adjacent row(s); only a price digit recovered"
                )
            else:
                probs = []
                if not _has(prices, g.price_resident_kzt):
                    probs.append(
                        f"res {g.price_resident_kzt:g} ∉ {[f'{p:g}' for p in prices]}"
                    )
                if g.price_nonresident_kzt is not None and not _has(
                    prices, g.price_nonresident_kzt
                ):
                    probs.append(
                        f"non {g.price_nonresident_kzt:g} ∉ {[f'{p:g}' for p in prices]}"
                    )
                m.status = "wrong" if probs else "ok"
                m.detail = "; ".join(probs)
        matches.append(m)
    return matches, used


# ── HTML rendering ───────────────────────────────────────────────────────────
_STATUS = {
    "ok": ("#1a7f37", "#dafbe1", "✓ correct"),
    "garbled": ("#9a6700", "#fff8c5", "≈ name lost"),
    "wrong": ("#cf222e", "#ffebe9", "✗ wrong"),
    "missing": ("#6e7781", "#f0f1f3", "— missing"),
}
_RAW_CAP = 90  # cap the raw-OCR dump so big docs don't blow up the HTML


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _fmt_prices(prices: list[float]) -> str:
    return ", ".join(f"{p:,.0f}".replace(",", " ") for p in prices) if prices else "—"


def _g_price(v: float | None) -> str:
    return f"{v:,.0f}".replace(",", " ") if v is not None else "—"


_CSS = """
:root{--mono:ui-monospace,'SF Mono',Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#1f2328;margin:0;background:#f6f8fa}
.wrap{max-width:1120px;margin:0 auto;padding:30px 24px 64px}
a{color:#0969da;text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:21px;margin:0 0 4px}h2{font-size:15px;margin:32px 0 10px}
.sub{color:#57606a;margin:0 0 18px;font-size:13px}
.sub code{font-family:var(--mono);background:#eaeef2;padding:1px 6px;border-radius:5px}
.cards{display:flex;gap:11px;flex-wrap:wrap;margin:0 0 20px}
.card{flex:1;min-width:104px;border:1px solid;border-radius:10px;padding:13px 15px}
.cv{font-size:25px;font-weight:700;line-height:1}.cl{font-size:12px;color:#57606a;margin-top:4px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d0d7de;border-radius:10px;overflow:hidden}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#57606a;
   background:#f6f8fa;padding:9px 12px;border-bottom:1px solid #d0d7de}
td{padding:8px 12px;border-bottom:1px solid #eaeef2;vertical-align:top}
tr:last-child td{border-bottom:none}
.num{color:#8c959f;font-family:var(--mono);font-size:12px;width:34px;text-align:right}
.svc{font-weight:500;max-width:330px}
.svc.ocr{font-family:var(--mono);font-size:12.5px;font-weight:400;color:#24292f}
.p{font-variant-numeric:tabular-nums;white-space:nowrap;text-align:right;font-weight:600;width:88px}
.p.ocr{font-weight:500;color:#24292f}
.arrow{color:#afb8c1;text-align:center;width:22px}
.badge{white-space:nowrap;font-size:12.5px;width:104px;font-weight:600}
.note{font-size:11.5px;color:#7a8290;font-weight:400;margin-top:2px}
.detail{font-size:11.5px;color:#cf222e;margin-top:3px;font-family:var(--mono)}
.dim{color:#afb8c1}
.noise{font-size:10.5px;color:#cf222e;background:#ffebe9;padding:1px 6px;border-radius:4px;margin-left:6px}
.legend{font-size:12px;color:#57606a;margin:10px 0 0;display:flex;gap:16px;flex-wrap:wrap}
.legend b{font-weight:600}
.dot{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:middle}
.foot{color:#8c959f;font-size:12px;margin-top:8px}
.bar{height:9px;border-radius:5px;background:#eaeef2;overflow:hidden;display:flex;min-width:120px}
.bar span{display:block;height:100%}
"""


def render_file(
    golden: GoldenFile,
    matches: list[Match],
    parsed: list[PriceRow],
    used: set[int],
    pdf_name: str,
    engine_note: str,
    coverage: tuple[int, int],
    stem: str,
) -> str:
    total = len(matches)
    ok = sum(m.status == "ok" for m in matches)
    garbled = sum(m.status == "garbled" for m in matches)
    wrong = sum(m.status == "wrong" for m in matches)
    missing = sum(m.status == "missing" for m in matches)
    acc = ok / total * 100 if total else 0.0
    pp, pt = coverage

    cards = [
        ("Golden rows", total, "#24292f", "#eaeef2"),
        ("Clean correct", ok, *_STATUS["ok"][:2][::-1]),
        ("Name lost", garbled, *_STATUS["garbled"][:2][::-1]),
        ("Wrong price", wrong, *_STATUS["wrong"][:2][::-1]),
        ("Missing", missing, *_STATUS["missing"][:2][::-1]),
        ("Usable", f"{acc:.0f}%", "#0969da", "#ddf4ff"),
    ]
    card_html = "".join(
        f'<div class="card" style="background:{bg};border-color:{fg}33">'
        f'<div class="cv" style="color:{fg}">{v}</div><div class="cl">{lbl}</div></div>'
        for lbl, v, fg, bg in cards
    )

    body = []
    for i, m in enumerate(matches, 1):
        fg, bg, badge = _STATUS[m.status]
        ocr_name = _e(m.row.name) if m.row else '<span class="dim">—</span>'
        ocr_prices = _fmt_prices(_row_prices(m.row)) if m.row else "—"
        gnote = f'<div class="note">{_e(m.g.note)}</div>' if m.g.note else ""
        detail = f'<div class="detail">{_e(m.detail)}</div>' if m.detail else ""
        body.append(
            f'<tr style="background:{bg}"><td class="num">{i}</td>'
            f'<td class="svc">{_e(m.g.name)}{gnote}</td>'
            f'<td class="p">{_g_price(m.g.price_resident_kzt)}</td>'
            f'<td class="p">{_g_price(m.g.price_nonresident_kzt)}</td>'
            f'<td class="arrow">→</td>'
            f'<td class="svc ocr">{ocr_name}{detail}</td>'
            f'<td class="p ocr">{ocr_prices}</td>'
            f'<td class="badge" style="color:{fg}">{badge}</td></tr>'
        )

    raw = []
    for i, r in enumerate(parsed[:_RAW_CAP]):
        unmatched = i not in used
        bg = "#fff8f8" if unmatched else "transparent"
        tag = '<span class="noise">unmatched</span>' if unmatched else ""
        raw.append(
            f'<tr style="background:{bg}"><td class="num">{i + 1}</td>'
            f'<td class="svc ocr">{_e(r.name)} {tag}</td>'
            f'<td class="p ocr">{_fmt_prices(_row_prices(r))}</td></tr>'
        )
    raw_more = (
        f'<p class="foot">… {len(parsed) - _RAW_CAP} more OCR rows not shown.</p>'
        if len(parsed) > _RAW_CAP
        else ""
    )

    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>{_e(stem)} — OCR vs golden</title><style>{_CSS}</style></head><body><div class="wrap">
<p class="sub"><a href="index.html">← all clinic 1–5 OCR previews</a></p>
<h1>{_e(golden.partner)} — scanned (OCR) vs golden standard</h1>
<p class="sub">Variation: <code>{_e(stem)}</code> &nbsp;·&nbsp; source: <code>{_e(pdf_name)}</code><br>
OCR engine: <code>{_e(engine_note)}</code> &nbsp;·&nbsp; pages OCR'd: <b>{pp}/{pt}</b>
&nbsp;·&nbsp; {len(parsed)} OCR rows vs {total} golden rows
{"&nbsp;·&nbsp; <b>golden is a doc-wide sample</b>" if golden.sampled else ""}</p>
<div class="cards">{card_html}</div>
<p class="legend">
<span><span class="dot" style="background:{_STATUS["ok"][0]}"></span><b>clean correct</b> — name &amp; price recovered</span>
<span><span class="dot" style="background:{_STATUS["garbled"][0]}"></span><b>name lost</b> — price survived, name merged into another row</span>
<span><span class="dot" style="background:{_STATUS["wrong"][0]}"></span><b>wrong</b> — matched by name, price differs</span>
<span><span class="dot" style="background:{_STATUS["missing"][0]}"></span><b>missing</b> — not recovered from OCR</span>
</p>
<p class="foot">“Usable” counts clean-correct only: a name-lost row can’t be attributed to a service, so its price is useless.</p>
<h2>Row-by-row: golden &nbsp;→&nbsp; scanned (OCR)</h2>
<table><thead><tr><th></th><th>Service (golden)</th><th>Res.</th><th>Non&#8209;res.</th><th></th>
<th>Scanned name (OCR)</th><th>OCR price(s)</th><th>Flag</th></tr></thead>
<tbody>{"".join(body)}</tbody></table>
<h2>Raw scan output — first {min(len(parsed), _RAW_CAP)} of {len(parsed)} OCR rows</h2>
<table><thead><tr><th></th><th>OCR name</th><th>OCR price(s)</th></tr></thead>
<tbody>{"".join(raw)}</tbody></table>{raw_more}
</div></body></html>"""


# ── per-file build (runs in a worker process) ────────────────────────────────
def build_one(stem: str, out_dir: str) -> dict:
    """OCR one golden's source, score it, write its HTML. Returns summary stats."""
    try:
        import torch

        torch.set_num_threads(3)  # 4 workers × 3 = 12 cores, no oversubscription
    except Exception:  # noqa: BLE001
        pass

    golden = load_golden(GOLDEN_DIR / f"{stem}.json")
    res_stat = {
        "stem": stem,
        "partner": golden.partner,
        "source": golden.source_file,
        "golden_rows": len(golden.rows),
        "sampled": golden.sampled,
    }
    src = resolve_source(golden.source_file)
    if src is None:
        res_stat["error"] = "source file not found in data/originals"
        return res_stat
    res_stat["fmt"] = src.suffix.lower()
    try:
        pdf = ensure_pdf(src)
    except Exception as e:  # noqa: BLE001
        res_stat["error"] = f"docx→pdf failed: {type(e).__name__}: {e}"
        return res_stat

    result = parse_file(pdf, file_format="scan_pdf")
    parsed = result.rows
    engine_note = next(
        (w for w in result.warnings if w.startswith("OCR engine")), "scan_pdf / ocr"
    )
    mcov = re.search(r"pages=(\d+)/(\d+)", engine_note)
    coverage = (int(mcov.group(1)), int(mcov.group(2))) if mcov else (0, 0)

    matches, used = align(golden.rows, parsed)
    out_path = Path(out_dir) / f"{stem}.html"
    out_path.write_text(
        render_file(
            golden, matches, parsed, used, src.name, engine_note[:90], coverage, stem
        ),
        encoding="utf-8",
    )
    res_stat.update(
        ocr_rows=len(parsed),
        pages=coverage,
        ok=sum(m.status == "ok" for m in matches),
        garbled=sum(m.status == "garbled" for m in matches),
        wrong=sum(m.status == "wrong" for m in matches),
        missing=sum(m.status == "missing" for m in matches),
        html=out_path.name,
    )
    return res_stat


def render_index(stats: list[dict], out_dir: Path) -> None:
    def cell(s, key):
        return s.get(key, 0)

    rows = []
    tot = {k: 0 for k in ("golden_rows", "ok", "garbled", "wrong", "missing")}
    for s in stats:
        if s.get("error"):
            rows.append(
                f'<tr><td class="svc"><a href="{s.get("html", "#")}">{_e(s["stem"])}</a></td>'
                f'<td>{_e(s["partner"])}</td><td colspan="7" class="detail">{_e(s["error"])}</td></tr>'
            )
            continue
        for k in tot:
            tot[k] += cell(s, k)
        gr = s["golden_rows"] or 1
        ok, ga, wr, mi = (
            cell(s, "ok"),
            cell(s, "garbled"),
            cell(s, "wrong"),
            cell(s, "missing"),
        )
        usable = ok / gr * 100
        pp, pt = s.get("pages", (0, 0))
        bar = (
            f'<div class="bar" title="correct/name-lost/wrong/missing">'
            f'<span style="width:{ok / gr * 100:.1f}%;background:{_STATUS["ok"][0]}"></span>'
            f'<span style="width:{ga / gr * 100:.1f}%;background:{_STATUS["garbled"][0]}"></span>'
            f'<span style="width:{wr / gr * 100:.1f}%;background:{_STATUS["wrong"][0]}"></span>'
            f'<span style="width:{mi / gr * 100:.1f}%;background:{_STATUS["missing"][0]}"></span></div>'
        )
        rows.append(
            f'<tr><td class="svc"><a href="{s["html"]}">{_e(s["stem"])}</a>'
            f'<div class="note">{_e(s["source"])}</div></td>'
            f"<td>{_e(s['partner'])}</td>"
            f'<td class="num">{s.get("fmt", "")}</td>'
            f'<td class="num">{pp}/{pt}</td>'
            f'<td class="num">{s["golden_rows"]}</td>'
            f'<td class="num" style="color:{_STATUS["ok"][0]};font-weight:700">{ok}</td>'
            f'<td class="num" style="color:{_STATUS["garbled"][0]}">{ga}</td>'
            f'<td class="num" style="color:{_STATUS["wrong"][0]}">{wr}</td>'
            f'<td class="num" style="color:{_STATUS["missing"][0]}">{mi}</td>'
            f'<td class="p">{usable:.0f}%</td><td style="min-width:130px">{bar}</td></tr>'
        )
    gr = tot["golden_rows"] or 1
    agg = (
        f'<tr style="background:#f6f8fa;font-weight:700"><td>TOTAL</td><td></td><td></td><td></td>'
        f'<td class="num">{tot["golden_rows"]}</td>'
        f'<td class="num" style="color:{_STATUS["ok"][0]}">{tot["ok"]}</td>'
        f'<td class="num" style="color:{_STATUS["garbled"][0]}">{tot["garbled"]}</td>'
        f'<td class="num" style="color:{_STATUS["wrong"][0]}">{tot["wrong"]}</td>'
        f'<td class="num" style="color:{_STATUS["missing"][0]}">{tot["missing"]}</td>'
        f'<td class="p">{tot["ok"] / gr * 100:.0f}%</td><td></td></tr>'
    )
    doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>OCR previews — clinics 1–5</title><style>{_CSS}</style></head><body><div class="wrap">
<h1>OCR vs golden standard — clinics 1–5, all variations</h1>
<p class="sub">Each source forced through the OCR path (<code>file_format=scan_pdf</code>, easyocr-ru, all pages).
“Usable” = clean name+price recovery. Click a variation for the row-by-row diff.</p>
<table><thead><tr><th>Variation</th><th>Partner</th><th>Fmt</th><th>Pages</th><th>Golden</th>
<th>✓ ok</th><th>≈ lost</th><th>✗ wrong</th><th>— miss</th><th>Usable</th><th>Breakdown</th></tr></thead>
<tbody>{"".join(rows)}{agg}</tbody></table>
<p class="legend">
<span><span class="dot" style="background:{_STATUS["ok"][0]}"></span><b>clean correct</b></span>
<span><span class="dot" style="background:{_STATUS["garbled"][0]}"></span><b>name lost</b> (price only)</span>
<span><span class="dot" style="background:{_STATUS["wrong"][0]}"></span><b>wrong price</b></span>
<span><span class="dot" style="background:{_STATUS["missing"][0]}"></span><b>missing</b></span>
</p>
<p class="foot">Golden files are doc-wide samples (~40 rows), so “missing” means OCR didn’t recover that service
even though all pages were read — it isolates table-reconstruction failures from character recognition.</p>
</div></body></html>"""
    (out_dir / "index.html").write_text(doc, encoding="utf-8")


# scan_preview runs from tests/eval; GOLDEN_DIR is tests/golden
GOLDEN_DIR = ROOT / "tests" / "golden"


def _batch(out_dir: Path, workers: int) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    print(
        f"OCR preview batch → {out_dir}  ({len(BATCH_GOLDENS)} variations, {workers} workers)"
    )
    stats: dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(build_one, stem, str(out_dir)): stem for stem in BATCH_GOLDENS
        }
        for fut in as_completed(futs):
            stem = futs[fut]
            try:
                s = fut.result()
            except Exception as e:  # noqa: BLE001
                s = {
                    "stem": stem,
                    "partner": "?",
                    "source": "?",
                    "golden_rows": 0,
                    "error": f"{type(e).__name__}: {e}",
                }
            stats[stem] = s
            if s.get("error"):
                print(f"  ✗ {stem}: {s['error']}")
            else:
                pp, pt = s.get("pages", (0, 0))
                print(
                    f"  ✓ {stem}: pages {pp}/{pt}, ocr_rows {s['ok'] + 0}, "
                    f"ok {s['ok']} lost {s['garbled']} wrong {s['wrong']} miss {s['missing']} "
                    f"/ {s['golden_rows']}"
                )
    ordered = [stats[s] for s in BATCH_GOLDENS if s in stats]
    render_index(ordered, out_dir)
    print(f"wrote {out_dir / 'index.html'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--batch", action="store_true", help="run all clinic 1–5 variations"
    )
    ap.add_argument("--out-dir", default="ocr_preview", help="batch output directory")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--golden", default="tests/golden/klinika_5_scan.json")
    ap.add_argument("--out", default="scan_preview.html")
    a = ap.parse_args(argv)

    if a.batch:
        return _batch(Path(a.out_dir), a.workers)

    # single-file mode (back-compat)
    stem = Path(a.golden).stem
    out_dir = Path(a.out).resolve().parent
    s = build_one(stem, str(out_dir))
    if s.get("error"):
        print(f"error: {s['error']}")
        return 1
    Path(out_dir / f"{stem}.html").replace(a.out)
    print(
        f"{stem}: ok {s['ok']} lost {s['garbled']} wrong {s['wrong']} miss {s['missing']} / {s['golden_rows']}"
    )
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
