"""Ingestion pipeline: one file -> Partner + PriceDocument + PriceItem rows.

Steps per file (TZ 4.1/4.2/4.4):
  detect format -> save original -> parse -> resolve partner -> dedup document by
  content hash -> for each row: map tariffs, convert currency, match catalogue,
  version against prior prices, validate -> write -> set document status.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import ORIGINALS_DIR, PRICE_ANOMALY_PCT
from .models import Partner, PriceDocument, PriceItem
from .normalize import ServiceMatcher, _key
from .parsers import parse_file
from .parsers.detect import (
    detect_format,
    effective_date_from_filename,
    partner_from_filename,
)
from .tariffs import map_tariffs, to_kzt
from .validation import is_droppable_name, validate_item


@dataclass
class IngestReport:
    file_name: str
    doc_id: uuid.UUID | None = None
    partner: str | None = None
    file_format: str | None = None
    status: str = "pending"
    rows_parsed: int = 0
    rows_written: int = 0
    rows_dropped: int = 0
    rows_needs_review: int = 0
    rows_matched: int = 0
    warnings: list[str] = field(default_factory=list)
    skipped_duplicate: bool = False

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["doc_id"] = str(self.doc_id) if self.doc_id else None
        return d


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def get_or_create_partner(session: Session, name: str) -> Partner:
    partner = session.scalar(select(Partner).where(Partner.name == name))
    if partner is None:
        partner = Partner(name=name)
        session.add(partner)
        session.flush()
    return partner


def ingest_file(
    session: Session,
    path: str | Path,
    *,
    original_name: str | None = None,
    force: bool = False,
) -> IngestReport:
    path = Path(path)
    file_name = original_name or path.name
    report = IngestReport(file_name=file_name)

    fmt = detect_format(path)
    report.file_format = fmt
    sha = _sha256(path)

    # Re-ingest dedup: same bytes already processed -> skip unless forced.
    existing = session.scalar(
        select(PriceDocument).where(PriceDocument.content_sha256 == sha)
    )
    if existing is not None and not force:
        report.doc_id = existing.doc_id
        report.status = existing.parse_status
        report.skipped_duplicate = True
        report.warnings.append("identical file already ingested; skipped")
        return report

    # Preserve the original file forever (TZ 5: исходные файлы не удаляются).
    saved = ORIGINALS_DIR / f"{sha[:16]}__{file_name}"
    if not saved.exists():
        shutil.copy2(path, saved)

    partner = get_or_create_partner(session, partner_from_filename(file_name))
    report.partner = partner.name
    eff_date = effective_date_from_filename(file_name)

    parsed = parse_file(path, file_format=fmt)
    report.rows_parsed = parsed.n_rows
    report.warnings.extend(parsed.warnings)

    doc = PriceDocument(
        partner_id=partner.partner_id,
        file_name=file_name,
        file_format=fmt,
        effective_date=eff_date,
        parse_status="processing",
        raw_content=parsed.raw_text[:1_000_000],
        content_sha256=sha,
    )
    session.add(doc)
    session.flush()
    report.doc_id = doc.doc_id

    matcher = ServiceMatcher(session)
    latest = _load_active_versions(session, partner.partner_id)

    log_lines: list[str] = list(parsed.warnings)
    any_review = False

    for row in parsed.rows:
        if is_droppable_name(row.name):
            report.rows_dropped += 1
            log_lines.append(f"skipped row without service name: {row.raw[:80]!r}")
            continue

        res_raw, non_raw = map_tariffs(row.prices, parsed.price_labels)
        resident = to_kzt(res_raw, row.currency)
        nonresident = to_kzt(non_raw, row.currency)

        key = (row.code or _key(row.name)).lower()
        prev = latest.get(key)
        is_active, supersede, prev_resident = _version_decision(prev, eff_date)

        reasons = validate_item(
            name=row.name,
            resident=resident,
            nonresident=nonresident,
            effective_date=eff_date,
            previous_resident=prev_resident,
            anomaly_pct=PRICE_ANOMALY_PCT,
        )
        match = matcher.match(row.name)

        item = PriceItem(
            doc_id=doc.doc_id,
            partner_id=partner.partner_id,
            service_name_raw=row.name,
            service_code_source=row.code,
            service_id=match.service_id,
            price_resident_kzt=resident,
            price_nonresident_kzt=nonresident,
            price_original=res_raw,
            currency_original=row.currency,
            match_method=match.method,
            match_confidence=match.confidence,
            needs_review=bool(reasons),
            review_reason="; ".join(reasons) or None,
            section=row.section,
            unit=row.unit,
            source_row=row.raw,
            effective_date=eff_date,
            is_active=is_active,
        )
        session.add(item)

        if supersede is not None:
            supersede.is_active = False
        if is_active:
            latest[key] = item
        if match.service_id is not None:
            report.rows_matched += 1
        if reasons:
            report.rows_needs_review += 1
            any_review = True
        report.rows_written += 1

    if report.rows_written == 0:
        doc.parse_status = "error"
        log_lines.append("no usable price rows extracted")
    elif any_review:
        doc.parse_status = "needs_review"
    else:
        doc.parse_status = "done"
    doc.parse_log = "\n".join(log_lines[:2000])
    report.status = doc.parse_status

    session.commit()
    return report


def _load_active_versions(
    session: Session, partner_id: uuid.UUID
) -> dict[str, PriceItem]:
    """version_key -> currently-active PriceItem, for cross-document price history."""
    items = session.scalars(
        select(PriceItem).where(
            PriceItem.partner_id == partner_id, PriceItem.is_active.is_(True)
        )
    )
    out: dict[str, PriceItem] = {}
    for it in items:
        key = (it.service_code_source or _key(it.service_name_raw)).lower()
        out[key] = it
    return out


def _version_decision(prev: PriceItem | None, new_date: date | None):
    """Return (new_is_active, item_to_supersede, previous_resident_for_anomaly)."""
    if prev is None:
        return True, None, None
    pd, nd = prev.effective_date, new_date
    prev_res = (
        float(prev.price_resident_kzt) if prev.price_resident_kzt is not None else None
    )
    if pd is None or nd is None or nd > pd:
        # newer (or undated) supersedes the previous active version
        return True, prev, prev_res
    if nd == pd:
        # same partner+service+date -> duplicate: archive old, keep new (no anomaly check)
        return True, prev, None
    # nd < pd: incoming is older history; keep prev active, store new as inactive
    return False, None, None
