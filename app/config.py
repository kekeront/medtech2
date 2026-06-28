"""Runtime configuration. Everything is overridable via environment variables."""

from __future__ import annotations

import os
from pathlib import Path

# Project paths
BASE_DIR = Path(__file__).resolve().parent.parent

# Load local secrets/overrides (GROQ_API_KEY, DATABASE_URL, …) from gitignored env files
# before reading any setting. Real environment variables always win (override=False).
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env.local", override=False)
    load_dotenv(BASE_DIR / ".env", override=False)
except ImportError:  # python-dotenv optional; env vars still work without it
    pass

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

# OCR ingest (TZ 4.2 — scanned PDFs). A text-PDF yielding fewer than OCR_MIN_ROWS rows
# is treated as an image scan and re-run through OCR. OCR_MAX_SECONDS enforces the TZ 5
# budget (≤3 min/doc) as a hard wall-clock stop — pagination halts and partial results
# are returned once it is exceeded; OCR_MAX_PAGES is a secondary page ceiling.
OCR_DPI = int(os.getenv("MEDARCHIVE_OCR_DPI", "300"))
OCR_MAX_PAGES = int(os.getenv("MEDARCHIVE_OCR_MAX_PAGES", "40"))
OCR_MAX_SECONDS = float(os.getenv("MEDARCHIVE_OCR_MAX_SECONDS", "170"))
OCR_MIN_ROWS = int(os.getenv("MEDARCHIVE_OCR_MIN_ROWS", "3"))

# OCR typo correction (Groq LLM / Yandex Speller). Opt-in: when enabled, OCR'd service
# names are post-corrected (Cyrillic misrecognitions: "Прнсм"->"Прием"). "llm" needs
# GROQ_API_KEY; degrades to a no-op if the backend is unavailable. Numbers are never touched.
OCR_CORRECT = os.getenv("MEDARCHIVE_OCR_CORRECT", "0").lower() in ("1", "true", "yes")
OCR_CORRECT_METHOD = os.getenv("MEDARCHIVE_OCR_CORRECT_METHOD", "llm")

# Text-PDF extraction engine. Default "gemini" (Vertex AI vision — reads the PAGE IMAGE, so it
# survives the corrupt OCR text layers these clinic PDFs ship with). Other options: "geometric"
# (fitz word geometry — FAST but garbage on scanned-with-bad-text PDFs), "surya"/"vlm"/"groq".
# NOTE: "gemini" needs GOOGLE_CLOUD_PROJECT + ADC; if a Gemini call fails, ingest falls back to
# the geometric text layer (fast but low quality) rather than crashing — so a misconfigured
# Gemini (bad model name, no auth) looks like a "fast but bad" parse. Keep GEMINI_MODEL valid.
PDF_ENGINE = os.getenv("MEDARCHIVE_PDF_ENGINE", "gemini")

# Groq OSS models for LLM extraction. Text path: production gpt-oss-120b (strong JSON /
# instruction following). Vision path: Qwen3-VL (best OSS document/table extraction).
# Qwen3-VL 27B handles both the text and vision paths — one lightweight multilingual
# model, best-in-class OSS document/table extraction.
GROQ_EXTRACT_MODEL = os.getenv("GROQ_EXTRACT_MODEL", "qwen/qwen3.6-27b")
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")
GROQ_EXTRACT_MAX_PAGES = int(os.getenv("GROQ_EXTRACT_MAX_PAGES", "40"))
# Concurrency / retry budget for Groq calls. Free tiers rate-limit hard (429), so keep
# concurrency low and lean on the SDK's retry-after backoff. Pages batched per call to
# cut request count (the RPM ceiling) at the cost of larger prompts (the TPM ceiling).
GROQ_EXTRACT_WORKERS = int(os.getenv("GROQ_EXTRACT_WORKERS", "1"))
GROQ_MAX_RETRIES = int(os.getenv("GROQ_MAX_RETRIES", "8"))
GROQ_PAGES_PER_CALL = int(os.getenv("GROQ_PAGES_PER_CALL", "3"))
# On a 429 the limit is per-minute, so wait for the window to refill (honour the server's
# retry-after header, else this many seconds) instead of failing fast.
GROQ_RATE_LIMIT_WAIT = float(os.getenv("GROQ_RATE_LIMIT_WAIT", "60"))

# Self-hosted vision model via Ollama (no API/rate limits, runs on the local GPU).
# Default: gemma3:4b — multilingual (incl. Russian) multimodal model whose loaded footprint
# fits an 8 GB GPU (Qwen2.5-VL-3B's ~10 GB does not, forcing slow CPU execution).
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_VLM_MODEL = os.getenv("OLLAMA_VLM_MODEL", "qwen2.5vl:3b")
# Local text LLM (Ollama) that structures Surya's OCR text into rows — light, fits the GPU.
# A num_predict cap is essential: without it qwen3's json_object decoding runs away to the
# context limit; with it the page's rows come back in a few seconds.
OLLAMA_TEXT_MODEL = os.getenv("OLLAMA_TEXT_MODEL", "qwen3:4b")
OLLAMA_TEXT_NUM_CTX = int(os.getenv("OLLAMA_TEXT_NUM_CTX", "8192"))
OLLAMA_TEXT_NUM_PREDICT = int(os.getenv("OLLAMA_TEXT_NUM_PREDICT", "4096"))
OLLAMA_VLM_DPI = int(os.getenv("OLLAMA_VLM_DPI", "130"))
# Cap the page image's longest side and the context window. High-resolution images make
# Qwen2.5-VL emit a large vision-token/compute buffer (~10 GB → spills to CPU on an 8 GB
# GPU); downscaling + a smaller context keeps the footprint on-GPU.
OLLAMA_VLM_MAX_PX = int(os.getenv("OLLAMA_VLM_MAX_PX", "1024"))
OLLAMA_VLM_NUM_CTX = int(os.getenv("OLLAMA_VLM_NUM_CTX", "4096"))
# Force GPU layer offload. Ollama 0.13.4+ mis-estimates qwen2.5-vl's compute graph
# (~1.8 GiB) at 6.7 GiB (ollama/ollama#13687), so it offloads 0 layers and falls back to
# CPU. num_gpu=99 overrides the bad estimate; the real ~5 GB footprint fits an 8 GB GPU.
OLLAMA_VLM_NUM_GPU = int(os.getenv("OLLAMA_VLM_NUM_GPU", "99"))

# Surya — light (650M) multilingual OCR engine (incl. Russian); a cooler/faster alternative
# to a 3B VLM for reading garbled/scanned PDFs. Runs on GPU (cap power to limit heat) or CPU.
# Small batch sizes keep VRAM/heat modest; set SURYA_DEVICE=cpu to take the GPU out entirely.
SURYA_DEVICE = os.getenv("SURYA_DEVICE", "cuda")
SURYA_DPI = int(os.getenv("SURYA_DPI", "150"))
SURYA_REC_BATCH = int(os.getenv("SURYA_REC_BATCH", "16"))
SURYA_DET_BATCH = int(os.getenv("SURYA_DET_BATCH", "4"))

# Tesseract — the ТЗ-named OCR engine. CPU-only, light, no GPU/heat. Needs the system
# binary (tesseract-ocr) + Russian data (tesseract-ocr-rus). PSM 6 = uniform text block.
TESSERACT_LANG = os.getenv("TESSERACT_LANG", "rus+eng")
TESSERACT_DPI = int(os.getenv("TESSERACT_DPI", "300"))
TESSERACT_PSM = int(os.getenv("TESSERACT_PSM", "6"))
TESSERACT_MIN_CONF = int(os.getenv("TESSERACT_MIN_CONF", "30"))

# Gemini via Vertex AI (GCP) — fast cloud path for the demo: send PDF chunks to
# gemini-2.5-flash, which does OCR + layout + structuring in one shot. Auth = Application
# Default Credentials (gcloud auth application-default login, or GOOGLE_APPLICATION_CREDENTIALS).
VERTEX_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
VERTEX_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
# .replace(",", ".") repairs the common "gemini-2,5-flash" comma typo — without it every Gemini
# call 404s and ingest silently degrades to the garbage text-layer parser ("fast but bad").
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").replace(",", ".").strip()
# Throughput: OUTPUT tokens are the real ceiling on long price lists (a dense page emits
# 30–45 rows), so keep chunks SMALL and run many in parallel. Few pages/call → little output
# per request (no truncation) and lots of independent chunks; high worker count → the whole
# 85-page doc finishes in ~2 concurrency waves. Raise WORKERS until Vertex starts returning
# 429s, then the backoff below absorbs them.
GEMINI_PAGES_PER_CALL = int(os.getenv("GEMINI_PAGES_PER_CALL", "2"))
GEMINI_WORKERS = int(os.getenv("GEMINI_WORKERS", "24"))
# Output headroom per call: a 2-page chunk (~70–90 rich rows) needs ample room or it truncates.
# High default so even a large pages-per-call chunk (e.g. an aggressive .env override) doesn't
# truncate mid-JSON and silently drop the rest of the page's rows.
GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "48000"))
# Thinking budget (tokens): 0 = off → fastest (default); -1 = automatic; N = explicit budget.
# Off is plenty for clean ruled tables; raise it if accuracy on faint scans needs a boost.
GEMINI_THINKING_BUDGET = int(os.getenv("GEMINI_THINKING_BUDGET", "0"))
# Per-chunk retry on transient errors (429 resource-exhausted, 5xx, timeouts). Linear backoff;
# a failed chunk degrades to 0 rows for that page-range rather than aborting the document.
GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "5"))
GEMINI_RETRY_WAIT = float(os.getenv("GEMINI_RETRY_WAIT", "12"))
GEMINI_TIMEOUT_MS = int(os.getenv("GEMINI_TIMEOUT_MS", "180000"))
# Max pages sent per PDF. Gemini does its own OCR+layout, so this is just a runaway guard —
# keep it well above the longest real price list (the 85-page Клиника 1) so nothing is dropped.
GEMINI_MAX_PAGES = int(os.getenv("GEMINI_MAX_PAGES", "300"))

# One-pass LLM data-quality cleanup of parsed rows BEFORE DB write (app/clean_llm.py). ON by
# default; fail-open (a no-op when Gemini isn't configured or a batch errors, so ingest never
# blocks). It cleans TEXT only (name/unit/section) and FLAGS suspicious price/tier/junk rows
# for the Проверка review page — it never edits numbers or codes.
GEMINI_CLEAN = os.getenv("MEDARCHIVE_GEMINI_CLEAN", "1").lower() in ("1", "true", "yes")
# Rows per cleanup call. Text-only output is small (~100 tok/row), so keep batches LARGE: each
# call costs one request against the (often tight, free-tier) Gemini rate limit, and the cleanup
# shares that budget with the heavier per-page EXTRACTION calls. Few big calls = far less quota
# contention, which is what was stalling bundle ingest. 200 rows ≈ 25k output tokens (< the 48k
# ceiling); a truncated/failed call just fails-open and keeps the parsed rows.
GEMINI_CLEAN_BATCH = int(os.getenv("GEMINI_CLEAN_BATCH", "200"))
# Cleanup concurrency — kept modest (independent of the 24-wide extraction pool) so a burst of
# cleanup calls doesn't spike 429s for the extractor running on the same quota.
GEMINI_CLEAN_WORKERS = int(os.getenv("GEMINI_CLEAN_WORKERS", "6"))

# FX rates to KZT used when a price is in a non-KZT currency (TZ 4.4).
# In production these would be looked up per effective_date; static defaults are fine for MVP.
FX_TO_KZT = {
    "KZT": 1.0,
    "USD": float(os.getenv("FX_USD_KZT", "525")),
    "RUB": float(os.getenv("FX_RUB_KZT", "5.7")),
}

# National Bank of Kazakhstan (NBK) live exchange-rate feeds.
# rates_all.xml — current/latest rates (RSS 2.0 format, per-item pubDate).
# get_rates.cfm  — historical rates, requires ?fdate=DD.MM.YYYY query param.
# The bare get_rates.cfm without fdate returns a JSON 500 error, NOT XML.
NBK_RATES_ALL_URL = os.getenv(
    "NBK_RATES_ALL_URL",
    "https://nationalbank.kz/rss/rates_all.xml",
)
NBK_FX_URL = os.getenv(
    "NBK_FX_URL",
    "https://nationalbank.kz/rss/get_rates.cfm",
)
# In-memory cache TTL for live NBK rates (seconds). NBK updates ~once per business day.
NBK_CACHE_TTL = int(os.getenv("NBK_CACHE_TTL", "3600"))

ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
