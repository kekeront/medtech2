"""Self-hosted vision extraction via Ollama (Qwen2.5-VL 3B on the local GPU).

Renders each PDF page to an image and asks a locally-served Qwen2.5-VL model for structured
rows — no API, no rate limits, data stays on the machine. Qwen2.5-VL is the best-in-class
small OSS document/table extractor, so the 3B fits an 8 GB GPU comfortably.

Reuses the extraction prompt and row parsing from pdf_groq (same contract: rows arrive with
`tariffs_resolved=True`). Pages run sequentially — one local GPU gains nothing from
concurrency and could OOM.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import fitz
import httpx

from ..config import (
    GROQ_EXTRACT_MAX_PAGES,
    OLLAMA_HOST,
    OLLAMA_VLM_DPI,
    OLLAMA_VLM_MAX_PX,
    OLLAMA_VLM_MODEL,
    OLLAMA_VLM_NUM_CTX,
    OLLAMA_VLM_NUM_GPU,
)
from .base import ParseResult
from .pdf_groq import _PROMPT_VISION, _parse_rows, _to_row

logger = logging.getLogger(__name__)


def _render_png_b64(page) -> str:
    """Render a page to PNG, downscaling so the longest side ≤ OLLAMA_VLM_MAX_PX — this
    bounds the VLM's vision-token count (and thus its VRAM footprint) for an 8 GB GPU."""
    pix = page.get_pixmap(matrix=fitz.Matrix(OLLAMA_VLM_DPI / 72, OLLAMA_VLM_DPI / 72))
    png = pix.tobytes("png")
    if max(pix.width, pix.height) > OLLAMA_VLM_MAX_PX:
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(png))
        img.thumbnail((OLLAMA_VLM_MAX_PX, OLLAMA_VLM_MAX_PX))
        buf = BytesIO()
        img.save(buf, format="PNG")
        png = buf.getvalue()
    return base64.b64encode(png).decode()


def ollama_available() -> tuple[bool, str]:
    """Return (available, detail): is the Ollama server up and the model present?"""
    try:
        r = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=3.0)
        r.raise_for_status()
        models = {m.get("name", "") for m in r.json().get("models", [])}
    except Exception as exc:  # noqa: BLE001
        return False, f"ollama unreachable at {OLLAMA_HOST}: {type(exc).__name__}"
    if OLLAMA_VLM_MODEL not in models and OLLAMA_VLM_MODEL + ":latest" not in models:
        return False, f"model {OLLAMA_VLM_MODEL!r} not pulled (have: {sorted(models)})"
    return True, OLLAMA_VLM_MODEL


def _extract_image(b64: str) -> list[dict]:
    payload = {
        "model": OLLAMA_VLM_MODEL,
        "stream": False,
        "format": "json",  # constrain Ollama to emit valid JSON
        "options": {
            "temperature": 0,
            "num_ctx": OLLAMA_VLM_NUM_CTX,
            "num_gpu": OLLAMA_VLM_NUM_GPU,  # force GPU offload past Ollama's bad estimate
        },
        "messages": [{"role": "user", "content": _PROMPT_VISION, "images": [b64]}],
    }
    r = httpx.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=300.0)
    r.raise_for_status()
    return _parse_rows(r.json().get("message", {}).get("content", ""))


def parse_pdf_vlm(path: str | Path, max_pages: int | None = None) -> ParseResult:
    doc = fitz.open(str(path))
    n_total = len(doc)
    limit = min(n_total, max_pages or GROQ_EXTRACT_MAX_PAGES)
    imgs = [_render_png_b64(doc[i]) for i in range(limit)]
    doc.close()

    result = ParseResult(file_format="pdf")
    for i, b64 in enumerate(imgs):
        try:
            for d in _extract_image(b64):
                row = _to_row(d)
                if row is not None:
                    result.rows.append(row)
        except Exception as exc:  # noqa: BLE001 — one bad page must not abort the doc
            logger.warning("Ollama VLM failed on page %d: %s", i + 1, exc)

    result.warnings.append(
        f"ollama vlm ({OLLAMA_VLM_MODEL}): {len(result.rows)} rows from {limit}/{n_total} pages"
    )
    if not result.rows:
        result.warnings.append("ollama vlm produced no rows")
    return result
