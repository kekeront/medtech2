"""FastAPI router exposing the OCR endpoints (POST /ocr, GET /ocr/health).

These are completely decoupled from the default parsing pipeline in app/parsers/*.
They are an optional, alternative path for true image scans that have no usable
embedded text layer.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from .ocr import ocr_engine_available, ocr_image, ocr_pdf
from .ocr_correction import correct_ocr_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ocr", tags=["ocr"])

_SUPPORTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif"}


@router.get("/health")
def ocr_health() -> dict:
    """Report whether the OCR engine is available and which engine is active."""
    available, engine = ocr_engine_available()
    return {"available": available, "engine": engine}


@router.post("")
async def ocr_upload(
    file: UploadFile = File(...),
    dpi: int = Query(300, ge=72, le=600, description="Render DPI for PDF pages"),
    max_pages: int | None = Query(
        None, ge=1, description="Limit number of PDF pages to OCR"
    ),
    structured: bool = Query(
        False,
        description=(
            "Attempt row/column reconstruction into price-row dicts (best-effort). "
            "Only meaningful for price-list layouts."
        ),
    ),
    correct: bool = Query(
        False,
        description=(
            "Post-process OCR text to fix recognition errors using an LLM or spell-checker. "
            "Adds 'corrected_text' and 'correction' keys to the response."
        ),
    ),
    correct_method: str = Query(
        "llm",
        description=(
            "Correction backend: 'llm' (Groq llama-3.3-70b-versatile, requires GROQ_API_KEY) "
            "or 'speller' (Yandex Speller, free, no key needed)."
        ),
    ),
) -> dict:
    """OCR a scanned PDF or image file.

    Returns extracted text per page plus optional structured price-row extraction.
    When ``correct=true``, the full OCR text is additionally post-processed to fix
    Cyrillic recognition errors — see ``correct_method`` for backend options.

    Supported file types: pdf, png, jpg, jpeg, tiff.
    """
    available, engine_info = ocr_engine_available()
    if not available:
        raise HTTPException(503, f"OCR engine unavailable: {engine_info}")

    name = file.filename or "upload"
    suffix = Path(name).suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise HTTPException(
            415,
            f"unsupported file type: {suffix!r}. Supported: {sorted(_SUPPORTED_SUFFIXES)}",
        )

    if correct and correct_method not in {"llm", "speller"}:
        raise HTTPException(
            422,
            f"unknown correct_method {correct_method!r}. Use 'llm' or 'speller'.",
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        if suffix == ".pdf":
            result = ocr_pdf(
                tmp_path, dpi=dpi, max_pages=max_pages, structured=structured
            )
        else:
            result = ocr_image(tmp_path, structured=structured)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"OCR failed: {type(exc).__name__}: {exc}") from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if correct:
        try:
            correction = correct_ocr_text(result["full_text"], method=correct_method)
            result["corrected_text"] = correction["corrected_text"]
            result["correction"] = {
                "method": correction["method"],
                "elapsed_sec": correction["elapsed_sec"],
                **(
                    {"warning": correction["warning"]}
                    if "warning" in correction
                    else {}
                ),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR correction failed (%s): %s", type(exc).__name__, exc)
            result["corrected_text"] = None
            result["correction"] = {
                "method": correct_method,
                "error": f"{type(exc).__name__}: {exc}",
            }

    return result
