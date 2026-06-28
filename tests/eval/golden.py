"""Golden reference schema + loader.

A golden file is a Claude-validated extraction of a clinic price list: the
ground truth the parser is scored against. It need not be exhaustive — a
representative, hand-verified sample per source file is enough to estimate
accuracy and surface systematic mismatches.

One JSON file per source document under tests/golden/, shape:

    {
      "source_file": "Клиника 8 2026.xlsx",
      "partner": "Клиника 8",
      "extracted_by": "claude",
      "sampled": true,
      "rows": [
        {"name": "Консультация врача: Аллерголог КМН",
         "code": "A02.020.000.2", "unit": "прием",
         "section": "ПРИЕМ ВРАЧА",
         "price_resident_kzt": 15480, "price_nonresident_kzt": null,
         "currency_original": "KZT"}
      ]
    }
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"


class GoldenRow(BaseModel):
    name: str
    code: str | None = None
    unit: str | None = None
    section: str | None = None
    price_resident_kzt: float | None = None
    price_nonresident_kzt: float | None = None
    price_extra_tiers: dict[str, float] | None = None
    currency_original: str = "KZT"
    # Optional: this row is *expected* to be ambiguous/dirty in the source, so the
    # parser is allowed (even required) to flag it for review rather than nail it.
    expect_review: bool = False
    note: str | None = None


class GoldenFile(BaseModel):
    source_file: str
    partner: str
    extracted_by: str = "claude"
    sampled: bool = True
    rows: list[GoldenRow] = Field(default_factory=list)


def load_golden(path: str | Path) -> GoldenFile:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return GoldenFile.model_validate(data)


def load_all_golden() -> list[GoldenFile]:
    return [load_golden(p) for p in sorted(GOLDEN_DIR.glob("*.json"))]
