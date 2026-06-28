"""FastAPI router for the structured extraction service (POST /extract, GET /extract/health).

A standalone microservice-style endpoint: upload a price-list file, get normalized price
rows back as JSON. It runs Groq (Qwen3-VL) as the primary extractor and transparently falls
back to the local parser stack when Groq is unavailable, rate-limited, or empty. Wired into
the app via app.include_router(extract_router) in api.py.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from . import extract_service

router = APIRouter(prefix="/extract", tags=["extract"])

_SUPPORTED = {".pdf", ".docx", ".xlsx", ".xls"}


@router.get("/health")
def extract_health() -> dict:
    """Report which extraction backends are available (Groq primary, local fallback)."""
    return extract_service.health()


@router.post("")
async def extract_endpoint(
    file: UploadFile = File(...),
    backend: str = Query(
        "auto",
        description="'auto' (Groq → local fallback), 'groq' (Groq only), or 'local'.",
    ),
    vision: bool = Query(
        False,
        description="Use the Qwen3-VL vision model on rendered pages (for garbled/scanned "
        "text layers) instead of the text layer. PDF only.",
    ),
) -> dict:
    """Extract structured price rows from one uploaded file.

    Returns the backend that produced the rows, the rows themselves (name / code / unit /
    section / resident & nonresident & extra-tier prices / currency), and — when Groq was
    skipped or fell back — the reason.
    """
    if backend not in ("auto", "groq", "local"):
        raise HTTPException(422, f"unknown backend {backend!r}; use auto/groq/local")

    name = file.filename or "upload"
    suffix = Path(name).suffix.lower()
    if suffix not in _SUPPORTED:
        raise HTTPException(415, f"unsupported file type: {suffix}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        result = extract_service.extract(tmp_path, backend=backend, vision=vision)
    except Exception as exc:  # noqa: BLE001 — surface extraction failures as 422
        raise HTTPException(
            422, f"extraction failed: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return {"file_name": name, **result.as_dict()}
