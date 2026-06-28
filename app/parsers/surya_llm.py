"""Surya OCR → local Qwen LLM structuring (via Ollama).

Plays each tool to its strength: Surya reads the scanned page faithfully (Russian incl.
degraded scans), then a small local text LLM (qwen3:4b) turns that OCR text into structured
rows — resolving names, units, sections and resident/nonresident tariffs itself. This avoids
the brittle geometric/column reassembly *and* the cloud rate limits: Surya can run on CPU
(cool) while the light text model runs on the GPU.

Reuses the extraction prompt + JSON parsing from pdf_groq (same row contract).
"""

from __future__ import annotations

import logging
from pathlib import Path

import fitz
import httpx

from ..config import (
    GROQ_EXTRACT_MAX_PAGES,
    OLLAMA_HOST,
    OLLAMA_TEXT_MODEL,
    OLLAMA_TEXT_NUM_CTX,
    OLLAMA_TEXT_NUM_PREDICT,
    OLLAMA_VLM_NUM_GPU,
    SURYA_DPI,
)
from .base import ParseResult
from .pdf_groq import _MAX_CHARS, _PROMPT, _SYSTEM, _parse_rows, _to_row
from .surya_ocr import _predictors, _render

logger = logging.getLogger(__name__)


def _page_texts(path: str | Path, max_pages: int | None) -> tuple[list[str], int]:
    """OCR each page with Surya → reading-order line text (one string per page)."""
    rec, det = _predictors()
    doc = fitz.open(str(path))
    n_total = len(doc)
    limit = min(n_total, max_pages or GROQ_EXTRACT_MAX_PAGES)
    images = [_render(doc[i], SURYA_DPI) for i in range(limit)]
    doc.close()

    results = rec(images, det_predictor=det, sort_lines=True)
    texts: list[str] = []
    for res in results:
        lines = getattr(res, "text_lines", []) or []
        texts.append("\n".join(t for line in lines if (t := (line.text or "").strip())))
    return texts, n_total


def _free_surya() -> None:
    """Release Surya's GPU memory after OCR so qwen3:4b can have the GPU for structuring —
    the two don't fit in 8 GB together, but they run in sequence, not at the same time."""
    import app.parsers.surya_ocr as so

    so._PRED = None
    try:
        import gc

        import torch

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass


def _structure(text: str) -> list[dict]:
    """Send one page's OCR text to the local Qwen model for structuring into rows."""
    if not text.strip():
        return []
    payload = {
        "model": OLLAMA_TEXT_MODEL,
        "stream": False,
        "format": "json",
        "think": False,  # qwen3 is a reasoning model; skip thinking for clean JSON
        "options": {
            "temperature": 0,
            "num_ctx": OLLAMA_TEXT_NUM_CTX,
            "num_predict": OLLAMA_TEXT_NUM_PREDICT,  # bound decoding or qwen3 runs away
            "num_gpu": OLLAMA_VLM_NUM_GPU,
        },
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _PROMPT.replace("<<TEXT>>", text[:_MAX_CHARS])},
        ],
    }
    try:
        r = httpx.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=300.0)
        r.raise_for_status()
        return _parse_rows(r.json().get("message", {}).get("content", ""))
    except Exception as exc:  # noqa: BLE001 — one page failing must not abort the doc
        logger.warning("surya_llm structuring failed: %s", exc)
        return []


def parse_pdf_surya_llm(path: str | Path, max_pages: int | None = None) -> ParseResult:
    texts, n_total = _page_texts(path, max_pages)
    _free_surya()  # hand the GPU to qwen for the structuring step (8 GB can't hold both)
    result = ParseResult(file_format="pdf", raw_text="\n\n".join(texts))
    for text in texts:
        for d in _structure(text):
            row = _to_row(d)
            if row is not None:
                result.rows.append(row)

    result.warnings.append(
        f"surya+{OLLAMA_TEXT_MODEL}: {len(result.rows)} rows from {len(texts)}/{n_total} pages"
    )
    if not result.rows:
        result.warnings.append("surya_llm produced no rows")
    return result
