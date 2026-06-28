"""OCR post-correction for Russian medical price-list text.

Two backends are available:

  * "llm"      — Llama-3.3-70b via the Groq SDK (fast inference, OSS model).
                 Understands context and fixes severe character-substitution errors
                 ("Прнсм" → "Прием").  Requires GROQ_API_KEY in the environment.
  * "speller"  — Yandex Speller public REST API (no API key, free).  Works well for
                 simple typing mistakes; less reliable for OCR misrecognitions.

Both backends preserve line structure and never alter numeric tokens (prices, codes).

Usage
-----
    from .ocr_correction import correct_ocr_text
    result = correct_ocr_text(raw_ocr_text, method="llm")
    # result["corrected_text"] is the cleaned string
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

# Tokens that should never be changed: pure numbers, decimal/space-separated prices,
# and simple alphanumeric codes (e.g. "А04.10.001").
_PRESERVE_RE = re.compile(r"^[\d][0-9 .,\-/]*$|^[A-ZА-ЯЁa-zа-яё]{1,5}\d[\d.\-]+$")


# ------------------------------------------------------------------------------- public API


def correct_ocr_text(text: str, method: str = "llm") -> dict[str, Any]:
    """Post-process raw OCR output to fix recognition errors.

    Args:
        text:   Raw OCR text (may contain Cyrillic misrecognitions).
        method: ``"llm"`` (default) or ``"speller"``.

    Returns a dict with keys:
        corrected_text  — the cleaned text string
        method          — backend actually used
        elapsed_sec     — wall-clock seconds for the correction call
        warning         — (optional) non-fatal issue, e.g. speller rate-limit
    """
    if not text or not text.strip():
        return {
            "corrected_text": text,
            "method": method,
            "elapsed_sec": 0.0,
        }

    t0 = time.perf_counter()
    warning: str | None = None

    if method == "speller":
        corrected, warning = _correct_with_speller(text)
    else:
        corrected = _correct_with_llm(text)

    out: dict[str, Any] = {
        "corrected_text": corrected,
        "method": method,
        "elapsed_sec": round(time.perf_counter() - t0, 3),
    }
    if warning:
        out["warning"] = warning
    return out


def groq_available() -> bool:
    """True if the Groq LLM backend can be used (package importable + API key set)."""
    if not os.getenv("GROQ_API_KEY"):
        return False
    try:
        import groq  # noqa: F401,PLC0415
    except ImportError:
        return False
    return True


# Each row name is one line; cap the batch so the corrected reply fits in max_tokens.
_NAME_BATCH = 120


def correct_names(names: list[str], method: str = "llm") -> list[str]:
    """Batch-correct a list of OCR'd service names, preserving order and count.

    One LLM call per ~120 names (newline-joined) instead of one per row. Degrades
    safely: if the backend is unavailable or a batch comes back with a different line
    count (so we can't realign), the original names are kept unchanged. Numbers are
    never altered (the prompt forbids it).
    """
    if not names:
        return names
    if method == "llm" and not groq_available():
        logger.warning("OCR name correction requested but Groq unavailable; skipping")
        return names

    out: list[str] = []
    for start in range(0, len(names), _NAME_BATCH):
        chunk = names[start : start + _NAME_BATCH]
        block = "\n".join(n.replace("\n", " ") for n in chunk)
        try:
            fixed = correct_ocr_text(block, method=method)["corrected_text"]
            lines = (fixed or "").split("\n")
        except Exception as exc:  # noqa: BLE001 — correction is best-effort
            logger.warning("OCR name correction batch failed: %s", exc)
            lines = []
        # Only trust the correction if the line count is preserved (realignable).
        if len(lines) == len(chunk):
            out.extend(c.strip() or orig for c, orig in zip(lines, chunk))
        else:
            out.extend(chunk)
    return out


# ------------------------------------------------------------------------------- LLM backend (Groq)

_GROQ_MODEL = "llama-3.3-70b-versatile"

_LLM_PROMPT = """\
You are correcting OCR recognition errors in a Russian medical clinic price list.
The text was scanned and OCR software misread some Cyrillic characters.

Rules (follow exactly):
1. Fix obvious OCR character-substitution errors in Russian words only.
   Common errors: н→и, р→п, е→с, etc. (e.g. "Прнсм" → "Прием", "Конснльтацня" → "Консультация").
2. Preserve ALL numeric tokens (prices, codes, phone numbers) EXACTLY — do not change any digit.
3. Preserve the original line structure (same number of lines, same order).
4. Do NOT add, remove, reorder, or merge lines.
5. Do NOT translate. Output Russian text only.
6. If a word looks correct already, leave it unchanged.

Return ONLY the corrected text. No explanations, no markdown, no extra lines.

OCR text:
{text}"""


def _correct_with_llm(text: str) -> str:
    """Send OCR text to Groq (llama-3.3-70b-versatile) for correction."""
    try:
        from groq import (
            Groq,  # noqa: PLC0415 — lazy: only if LLM correction is requested
        )
    except ImportError as exc:
        raise ImportError(
            "The 'groq' package is required for LLM correction. "
            "Install it with: uv add groq"
        ) from exc

    client = Groq()  # reads GROQ_API_KEY from env

    logger.info(
        "Sending %d chars to Groq (%s) for OCR correction", len(text), _GROQ_MODEL
    )
    response = client.chat.completions.create(
        model=_GROQ_MODEL,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": _LLM_PROMPT.format(text=text),
            }
        ],
    )

    corrected: str = response.choices[0].message.content or ""
    logger.debug(
        "OCR correction done — input %d chars, output %d chars, "
        "finish_reason=%s, input_tokens=%d, output_tokens=%d",
        len(text),
        len(corrected),
        response.choices[0].finish_reason,
        response.usage.prompt_tokens if response.usage else 0,
        response.usage.completion_tokens if response.usage else 0,
    )
    return corrected


# ------------------------------------------------------------------------------- Yandex Speller backend


_SPELLER_URL = "https://speller.yandex.net/services/spellservice.json/checkText"
# options bitmask: 1=IGNORE_DIGITS (skip tokens that contain digits)
_SPELLER_OPTIONS = 1


def _correct_with_speller(text: str) -> tuple[str, str | None]:
    """Use Yandex Speller (free, no key) to fix Russian spelling errors.

    Returns (corrected_text, warning_or_None).
    """
    try:
        import httpx  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "The 'httpx' package is required for speller correction. "
            "Install it with: uv add httpx"
        ) from exc

    lines = text.split("\n")
    corrected_lines: list[str] = []
    warning: str | None = None

    with httpx.Client(timeout=10.0) as client:
        for line in lines:
            if not line.strip():
                corrected_lines.append(line)
                continue

            try:
                resp = client.get(
                    _SPELLER_URL,
                    params={"text": line, "lang": "ru", "options": _SPELLER_OPTIONS},
                )
                resp.raise_for_status()
                corrections: list[dict[str, Any]] = resp.json()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Yandex Speller error on line %r: %s", line[:40], exc)
                warning = f"Speller unavailable: {exc}"
                corrected_lines.append(line)
                continue

            if not corrections:
                corrected_lines.append(line)
                continue

            # Apply corrections right-to-left to keep positions valid.
            corrected_line = line
            for fix in sorted(corrections, key=lambda x: x["pos"], reverse=True):
                suggestions: list[str] = fix.get("s", [])
                if not suggestions:
                    continue
                pos: int = fix["pos"]
                length: int = fix["len"]
                original_token = line[pos : pos + length]

                # Never touch tokens that look like prices, codes, or numbers.
                if _PRESERVE_RE.match(original_token):
                    continue

                corrected_line = (
                    corrected_line[:pos]
                    + suggestions[0]
                    + corrected_line[pos + length :]
                )

            corrected_lines.append(corrected_line)

    return "\n".join(corrected_lines), warning
