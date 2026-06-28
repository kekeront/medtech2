"""Archive ingestion helpers — extraction routing, safety, and filename recovery.

DB-free: exercises extract_archive / collect_documents / ensure_parseable directly with
in-memory zips. The DB-backed ingest_zip loop is covered by the eval pipeline.
"""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

import pytest

from app.archive import (
    _decode_zip_name,
    collect_documents,
    ensure_parseable,
    extract_archive,
)


def _zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)


def test_extract_collect_routes_and_skips_junk_and_nested():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        nested = io.BytesIO()
        with zipfile.ZipFile(nested, "w") as nz:
            nz.writestr("Клиника 9.docx", b"x")  # Cyrillic name inside a nested zip
        _zip(
            td / "bundle.zip",
            {
                "a.pdf": b"%PDF",
                "sub/b.xlsx": b"PK",
                "c.xls": b"xls",
                "d.doc": b"doc",
                "notes.txt": b"ignore",  # unsupported -> skipped
                "__MACOSX/e.pdf": b"junk",  # macOS cruft -> skipped
                "._hidden.pdf": b"junk",  # AppleDouble -> skipped
                "inner.zip": nested.getvalue(),  # nested zip -> expanded
            },
        )
        dest = td / "out"
        dest.mkdir()
        extract_archive(td / "bundle.zip", dest)
        names = sorted(p.name for p in collect_documents(dest))
        assert names == ["a.pdf", "b.xlsx", "c.xls", "d.doc", "Клиника 9.docx"]


def test_zip_slip_is_blocked():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        with zipfile.ZipFile(td / "evil.zip", "w") as z:
            z.writestr("../escape.txt", b"pwned")
        dest = td / "out"
        dest.mkdir()
        with pytest.raises(ValueError, match="unsafe path"):
            extract_archive(td / "evil.zip", dest)


def test_cyrillic_filename_recovered_from_cp866():
    # zipfile exposes a non-UTF8 name as cp437-decoded bytes; we recover the real cp866 text.
    name = "Клиника 5 прайс.xlsx"
    info = zipfile.ZipInfo()
    info.filename = name.encode("cp866").decode("cp437")
    info.flag_bits = 0
    assert _decode_zip_name(info) == name


def test_utf8_flagged_name_passes_through():
    info = zipfile.ZipInfo()
    info.filename = "Клиника 1.pdf"
    info.flag_bits = 0x800  # UTF-8 flag set
    assert _decode_zip_name(info) == "Клиника 1.pdf"


def test_ensure_parseable_passthrough_for_non_doc():
    assert ensure_parseable(Path("/x/y.pdf")) == Path("/x/y.pdf")
    assert ensure_parseable(Path("/x/y.xlsx")) == Path("/x/y.xlsx")
