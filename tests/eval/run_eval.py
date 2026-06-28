"""Parser-accuracy eval pipeline.

    reset test DB -> for each file: ingest (real parser) -> diff vs golden
    -> score -> print results -> reset

Usage:
    uv run python -m tests.eval.run_eval                 # all golden files
    uv run python -m tests.eval.run_eval --keep          # leave test DB populated
    uv run python -m tests.eval.run_eval --only klinika_8
    uv run python -m tests.eval.run_eval --min-accuracy 0.95

Exit 0 if effective accuracy ≥ --min-accuracy and there are no silent errors;
1 otherwise; 2 if no golden references / source files were found.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select  # noqa: E402

from app.models import PriceItem  # noqa: E402
from app.pipeline import ingest_file  # noqa: E402

from .compare import FileScore, compare  # noqa: E402
from .golden import GOLDEN_DIR, load_all_golden  # noqa: E402
from .testdb import TestSessionLocal, reset  # noqa: E402

_TTY = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def _find_source(archive: Path, name: str) -> Path | None:
    direct = archive / name
    if direct.exists():
        return direct
    # tolerate case/extension drift (e.g. .PDF vs .pdf)
    for p in archive.iterdir():
        if p.name.lower() == name.lower():
            return p
    return None


def run(args: argparse.Namespace) -> int:
    archive = Path(args.archive)
    if not archive.is_absolute():
        archive = Path(__file__).resolve().parents[2] / archive

    goldens = load_all_golden()
    if args.only:
        wanted = {n.removesuffix(".json") for n in args.only}
        goldens = [
            g
            for g, p in zip(goldens, sorted(GOLDEN_DIR.glob("*.json")))
            if p.stem in wanted
        ]
    if not goldens:
        print(_c("31", f"no golden references found in {GOLDEN_DIR}"))
        return 2

    print(_c("1;36", f"\n== Parser accuracy eval ({len(goldens)} file(s)) =="))
    print(_c("2", "reset → ingest → diff vs golden → score → reset\n"))

    reset()  # clean slate
    scores: list[FileScore] = []
    with TestSessionLocal() as db:
        for g in goldens:
            src = _find_source(archive, g.source_file)
            if src is None:
                print(_c("31", f"  ✗ source not found: {g.source_file}"))
                continue
            print(
                _c("1", f"  ▶ {g.source_file}")
                + _c("2", f"  ({len(g.rows)} golden rows)")
            )
            try:
                report = ingest_file(db, src, force=True)
            except Exception as e:  # noqa: BLE001
                db.rollback()
                print(_c("31", f"    ingest failed: {type(e).__name__}: {e}"))
                continue
            items = list(
                db.scalars(select(PriceItem).where(PriceItem.doc_id == report.doc_id))
            )
            score = compare(g, items)
            scores.append(score)
            _print_file(score, report.rows_written, verbose=args.verbose)

    rc = _print_summary(scores, args.min_accuracy)

    if not args.keep:
        reset()
        print(_c("2", "\ntest DB reset."))
    else:
        print(_c("2", "\ntest DB left populated (--keep)."))
    return rc


def _print_file(s: FileScore, written: int, verbose: bool) -> None:
    acc, eff = s.accuracy * 100, s.effective_accuracy * 100
    colour = "32" if eff >= 95 else ("33" if eff >= 80 else "31")
    print(
        f"    extracted {written} rows · "
        f"accuracy {_c(colour, f'{acc:.1f}%')} "
        f"(effective {eff:.1f}%) · "
        f"correct {s.correct}/{s.total} · missing {s.missing} · "
        f"flagged-wrong {s.flagged_errors} · "
        + _c("31" if s.silent_errors else "2", f"silent-wrong {s.silent_errors}")
    )
    bad = [r for r in s.rows if not r.ok]
    for r in bad if verbose else bad[:8]:
        tag = "SILENT" if r.silent_error else ("queued" if r.flagged else r.status)
        detail = "; ".join(r.issues) or r.status
        print(
            f"        {_c('31' if r.silent_error else '33', tag):<8} "
            f"{r.golden.name[:46]!r} — {detail}"
        )
    if not verbose and len(bad) > 8:
        print(_c("2", f"        … {len(bad) - 8} more (use --verbose)"))


def _print_summary(scores: list[FileScore], min_acc: float) -> int:
    print(_c("1", "\n" + "─" * 64))
    if not scores:
        print(_c("31", "  no files scored"))
        return 2
    total = sum(s.total for s in scores)
    correct = sum(s.correct for s in scores)
    missing = sum(s.missing for s in scores)
    silent = sum(s.silent_errors for s in scores)
    flagged = sum(s.flagged_errors for s in scores)
    eff_good = sum(
        1
        for s in scores
        for r in s.rows
        if r.ok or (r.matched and r.flagged and not r.ok)
    )
    acc = correct / total if total else 0.0
    eff_acc = eff_good / total if total else 0.0

    print(f"  golden rows:        {total}")
    print(f"  correct:            {correct}  ({acc * 100:.1f}%)")
    print(f"  routed to review:   {flagged}")
    print(f"  missing (not found):{missing}")
    print(f"  {_c('31' if silent else '32', f'silent errors:      {silent}')}")
    print(
        f"  {_c('1', 'effective accuracy:')} "
        f"{_c('32' if eff_acc >= min_acc else '31', f'{eff_acc * 100:.1f}%')}  "
        f"(target ≥{min_acc * 100:.0f}%)"
    )

    passed = eff_acc >= min_acc and silent == 0
    if passed:
        print(_c("1;32", "\n  RESULT: PASS"))
        return 0
    why = []
    if eff_acc < min_acc:
        why.append(f"accuracy {eff_acc * 100:.1f}% < {min_acc * 100:.0f}%")
    if silent:
        why.append(f"{silent} silent error(s) — wrong but not flagged for review")
    print(_c("1;31", f"\n  RESULT: FAIL ({'; '.join(why)})"))
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Score parser accuracy vs Claude golden references."
    )
    p.add_argument(
        "--archive", default="Хакатон", help="folder with the source price-list files"
    )
    p.add_argument("--only", nargs="*", help="golden stems to run (e.g. klinika_8)")
    p.add_argument(
        "--min-accuracy", type=float, default=0.95, help="effective-accuracy gate"
    )
    p.add_argument(
        "--keep", action="store_true", help="do not reset the test DB at the end"
    )
    p.add_argument("--verbose", action="store_true", help="list every mismatch")
    return run(p.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
