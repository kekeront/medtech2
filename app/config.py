"""Runtime configuration. Everything is overridable via environment variables."""

from __future__ import annotations

import os
from pathlib import Path

# Project paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("MEDARCHIVE_DATA_DIR", BASE_DIR / "data"))
ORIGINALS_DIR = DATA_DIR / "originals"  # kept forever (TZ: исходные файлы не удаляются)

# Database — defaults to the local Postgres role created during setup.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://medarchive:medarchive@localhost:5432/medarchive",
)

# Normalization thresholds (TZ 4.3): >= AUTO -> auto-match, else -> review queue.
MATCH_AUTO_THRESHOLD = float(os.getenv("MEDARCHIVE_MATCH_AUTO", "0.85"))
MATCH_SUGGEST_THRESHOLD = float(os.getenv("MEDARCHIVE_MATCH_SUGGEST", "0.55"))

# Validation (TZ 4.4)
PRICE_ANOMALY_PCT = float(
    os.getenv("MEDARCHIVE_PRICE_ANOMALY_PCT", "0.50")
)  # >50% change

# FX rates to KZT used when a price is in a non-KZT currency (TZ 4.4).
# In production these would be looked up per effective_date; static defaults are fine for MVP.
FX_TO_KZT = {
    "KZT": 1.0,
    "USD": float(os.getenv("FX_USD_KZT", "525")),
    "RUB": float(os.getenv("FX_RUB_KZT", "5.7")),
}

ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
