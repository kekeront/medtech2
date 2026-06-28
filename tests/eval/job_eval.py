"""Score a LIVE ingestion job (POST /upload) against the golden references.

Unlike run_eval (which re-ingests from an archive folder), this reads what an actual
running server already wrote: it pulls the job's per-file reports (file_name + doc_id) from
GET /jobs/{id}, loads each doc's PriceItems straight from the DB, and runs the standard
compare(). Use it to measure how the live pipeline (e.g. PDF_ENGINE=gemini) did vs ground truth.

    uv run python -m tests.eval.job_eval <job_id> [base_url]
"""

from __future__ import annotations

import sys
import uuid

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.models import PriceItem
from tests.eval.compare import compare
from tests.eval.golden import load_all_golden
from tests.eval.run_eval import _print_file, _print_summary


def run(job_id: str, base: str, min_acc: float = 0.90) -> int:
    v = httpx.get(f"{base}/jobs/{job_id}", timeout=20).json()
    print(
        f"job {job_id}: status={v['status']}  {v['done']}/{v['total']}  "
        f"rows_written={v['rows_written']}  errors={v['errors']}  ({v['elapsed_sec']}s)"
    )
    if v["status"] not in ("done", "error"):
        print("  job is still running — re-run once it finishes.")
        return 2

    reports: dict[str, dict] = {}
    for r in v.get("reports", []):
        reports[r["file_name"]] = r
        reports[r["file_name"].lower()] = r

    goldens = load_all_golden()
    scores = []
    with SessionLocal() as db:
        for g in goldens:
            rep = reports.get(g.source_file) or reports.get(g.source_file.lower())
            if not rep or not rep.get("doc_id"):
                continue  # this golden's source wasn't in the uploaded bundle
            items = list(
                db.scalars(
                    select(PriceItem).where(
                        PriceItem.doc_id == uuid.UUID(rep["doc_id"])
                    )
                )
            )
            score = compare(g, items)
            scores.append(score)
            print(
                f"\n  ▶ {g.source_file}  "
                f"(golden {len(g.rows)} rows · ingested {len(items)})"
            )
            _print_file(score, rep.get("rows_written", 0), verbose=False)

    if not scores:
        print("\nno golden matched any ingested file in this job.")
        return 2
    return _print_summary(scores, min_acc)


if __name__ == "__main__":
    job = sys.argv[1] if len(sys.argv) > 1 else "8958cac01138"
    base = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8078"
    raise SystemExit(run(job, base))
