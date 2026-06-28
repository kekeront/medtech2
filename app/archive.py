"""ZIP-archive ingestion: unzip an upload and route every document to the right parser.

A bundle of price lists arrives as a single .zip. We extract it (safely — no zip-slip),
walk it recursively (expanding nested zips), and hand each supported document to the normal
`ingest_file` pipeline, which detects the format and dispatches it:

    .xlsx / .xls            -> spreadsheet parser
    .docx                   -> word parser
    .doc  (legacy binary)   -> converted to .docx via LibreOffice first (no native reader)
    .pdf                    -> OCR parser for image scans, text/LLM parser for digital PDFs
                               (decided per-file by detect_format)

One malformed or unreadable file never aborts the batch — it becomes an `error` report.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

from .pipeline import IngestReport, ingest_file

# Everything the downstream parsers can handle (directly or after conversion).
SUPPORTED_SUFFIXES = {".pdf", ".docx", ".xlsx", ".xls", ".doc"}

# LibreOffice is used only to rescue legacy binary .doc (python-docx can't read it).
_LIBREOFFICE = shutil.which("libreoffice") or shutil.which("soffice")


def _is_junk(name: str) -> bool:
    """Archive cruft to ignore: macOS resource forks, Finder metadata, hidden dotfiles."""
    return any(
        part == "__MACOSX" or part == ".DS_Store" or part.startswith("._")
        for part in Path(name).parts
    )


def _decode_zip_name(info: zipfile.ZipInfo) -> str:
    """Recover a member's real name. zipfile decodes as cp437 unless the UTF-8 flag (bit 11)
    is set; Cyrillic archives commonly store utf-8 or cp866 bytes, so re-decode those."""
    if info.flag_bits & 0x800:  # UTF-8 flag already set
        return info.filename
    raw = info.filename.encode("cp437", "replace")
    for enc in ("utf-8", "cp866"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def extract_archive(zip_path: str | Path, dest: Path) -> None:
    """Extract a zip into `dest`, guarding against zip-slip (members escaping the dir) and
    recovering non-ASCII filenames. Directories and junk entries are skipped."""
    dest = Path(dest).resolve()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = _decode_zip_name(info)
            if _is_junk(name):
                continue
            target = (dest / name).resolve()
            if not target.is_relative_to(dest):  # zip-slip: '../' escape
                raise ValueError(f"unsafe path in archive: {info.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def collect_documents(root: Path) -> list[Path]:
    """Every supported document under `root`, recursively, expanding any nested .zip in place.
    Returned sorted for deterministic ingestion order."""
    docs: list[Path] = []
    for p in sorted(Path(root).rglob("*")):
        if not p.is_file() or _is_junk(str(p.relative_to(root))):
            continue
        suf = p.suffix.lower()
        if suf == ".zip":
            nested = p.parent / f"{p.stem}__unzipped"
            nested.mkdir(exist_ok=True)
            try:
                extract_archive(p, nested)
                docs.extend(collect_documents(nested))
            except (zipfile.BadZipFile, ValueError, OSError):
                continue  # a broken nested archive shouldn't sink the rest
        elif suf in SUPPORTED_SUFFIXES:
            docs.append(p)
    return docs


def _libreoffice_convert(src: Path, to: str) -> Path:
    out_dir = src.parent
    # Unique profile dir so concurrent conversions never clash on the LibreOffice lock.
    prof = out_dir / f".lo_{src.stem[:8]}"
    subprocess.run(
        [
            _LIBREOFFICE,
            f"-env:UserInstallation=file://{prof}",
            "--headless",
            "--convert-to",
            to,
            "--outdir",
            str(out_dir),
            str(src),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    out = out_dir / f"{src.stem}.{to}"
    if not out.exists():
        raise RuntimeError(f"LibreOffice did not produce {out.name}")
    return out


def ensure_parseable(path: Path) -> Path:
    """Return a path the parsers can read. Legacy binary `.doc` has no native reader, so it is
    converted to `.docx` via LibreOffice; every other format is returned unchanged."""
    if path.suffix.lower() != ".doc":
        return path
    if not _LIBREOFFICE:
        raise RuntimeError(
            "legacy .doc needs LibreOffice for conversion (libreoffice/soffice not found)"
        )
    return _libreoffice_convert(path, "docx")


def ingest_zip(
    session: Session,
    zip_path: str | Path,
    *,
    force: bool = False,
    force_ocr: bool = False,
    on_file: Callable[[IngestReport | None, int, int], None] | None = None,
) -> list[IngestReport]:
    """Extract a zip and ingest every supported document inside it. Office docs and PDFs both
    flow through `ingest_file`, which detects each format and parses accordingly. A per-file
    failure is isolated into an `error` report so the rest of the bundle still ingests.

    `on_file` is an optional progress callback for async drivers: it's called once with
    (None, 0, total) after the archive is scanned, then (report, done, total) after each
    document finishes — letting a job poller report live progress."""
    reports: list[IngestReport] = []
    with tempfile.TemporaryDirectory(prefix="medarchive_zip_") as tmp:
        root = Path(tmp)
        extract_archive(zip_path, root)
        docs = collect_documents(root)
        total = len(docs)
        if on_file is not None:
            on_file(None, 0, total)
        for i, doc in enumerate(docs):
            try:
                parseable = ensure_parseable(doc)
                rep = ingest_file(
                    session,
                    parseable,
                    original_name=doc.name,
                    force=force,
                    force_ocr=force_ocr,
                )
            except Exception as e:  # noqa: BLE001 — isolate per-file failures
                session.rollback()
                rep = IngestReport(
                    file_name=doc.name,
                    status="error",
                    warnings=[f"{type(e).__name__}: {e}"],
                )
            reports.append(rep)
            if on_file is not None:
                try:
                    on_file(rep, i + 1, total)
                except Exception:  # noqa: BLE001 — progress reporting must never break ingest
                    pass
    return reports
