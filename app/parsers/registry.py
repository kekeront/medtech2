"""Dispatch a file to the right parser based on detected format."""

from __future__ import annotations

from pathlib import Path

from .base import ParseResult
from .detect import detect_format


def parse_file(
    path: str | Path,
    file_format: str | None = None,
    ocr: bool = False,
    ocr_max_pages: int | None = None,
    correct: bool | None = None,
) -> ParseResult:
    fmt = file_format or detect_format(path)
    if fmt in ("pdf", "scan_pdf"):
        # Image-only scans (or an explicit OCR request) go through the OCR parser;
        # text PDFs keep the faster embedded-text geometric path.
        if ocr or fmt == "scan_pdf":
            from .ocr_pdf import parse_pdf_ocr

            return parse_pdf_ocr(path, max_pages=ocr_max_pages, correct=correct)
        from ..config import PDF_ENGINE

        if PDF_ENGINE == "surya":
            from .surya_ocr import parse_pdf_surya

            return parse_pdf_surya(path, max_pages=ocr_max_pages)
        if PDF_ENGINE == "surya_llm":
            from .surya_llm import parse_pdf_surya_llm

            return parse_pdf_surya_llm(path, max_pages=ocr_max_pages)
        if PDF_ENGINE == "tesseract":
            from .tesseract_ocr import parse_pdf_tesseract

            return parse_pdf_tesseract(path, max_pages=ocr_max_pages)
        if PDF_ENGINE == "vlm":
            from .vlm_ollama import parse_pdf_vlm

            return parse_pdf_vlm(path, max_pages=ocr_max_pages)
        if PDF_ENGINE == "gemini":
            from .gemini_pdf import parse_pdf_gemini

            return parse_pdf_gemini(path, max_pages=ocr_max_pages)
        if PDF_ENGINE == "groq":
            from .pdf_groq import parse_pdf_groq

            return parse_pdf_groq(path, max_pages=ocr_max_pages)
        from .pdf import parse_pdf

        return parse_pdf(path, file_format=fmt)
    if fmt == "docx":
        from .docx import parse_docx

        return parse_docx(path)
    if fmt in ("xlsx", "xls"):
        from .excel import parse_excel

        return parse_excel(path, file_format=fmt)
    raise ValueError(f"No parser for format: {fmt}")
