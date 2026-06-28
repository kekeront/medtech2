"""Target service catalogue: import (TZ 2.2), bootstrap (TZ 7) and re-matching (TZ 4.3).

The catalogue is the reference list every extracted position is normalized against.
Organizers provide it as XLSX/JSON; until then `bootstrap_catalogue` synthesizes one
from the extracted data so the normalization flow and the /unmatched→/match queue work.
After any catalogue change, `rematch` links existing price items to services.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import PriceItem, Service
from .normalize import ServiceMatcher, _key


# --------------------------------------------------------------------------- import
def _coerce_synonyms(value) -> list[str]:
    """Accept a JSON list, or a ';' / ',' / '|'-separated string of synonyms."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return []
    if s.startswith("["):
        try:
            return [str(v).strip() for v in json.loads(s) if str(v).strip()]
        except json.JSONDecodeError:
            pass
    for sep in (";", "|", ","):
        if sep in s:
            return [p.strip() for p in s.split(sep) if p.strip()]
    return [s]


def _parse_uuid(value):
    """Best-effort UUID from a catalogue cell; None if absent or malformed."""
    if not value or str(value).strip().lower() in ("", "nan"):
        return None
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, AttributeError):
        return None


def _upsert_service(
    session, index: dict, name, synonyms, category, icd, service_id=None
) -> bool:
    """Insert or update a catalogue service keyed by normalized name. Returns True if new.

    `index` maps normalized-name -> Service and is updated in place so a whole import
    runs in O(n) rather than re-scanning the table per row. An organizer-provided
    service_id (TZ 2.2) is honored on insert.
    """
    name = (name or "").strip()
    if not name:
        return False
    key = _key(name)
    syns = _coerce_synonyms(synonyms)
    existing = index.get(key)
    if existing is not None:
        existing.synonyms = list(dict.fromkeys((existing.synonyms or []) + syns))
        existing.category = existing.category or (category or None)
        existing.icd_code = existing.icd_code or (icd or None)
        return False
    svc = Service(
        service_name=name,
        synonyms=syns,
        category=(category or None),
        icd_code=(icd or None),
    )
    sid = _parse_uuid(service_id)
    if sid is not None:
        svc.service_id = sid
    session.add(svc)
    index[key] = svc
    return True


def load_catalogue_from_file(session: Session, path: str | Path) -> int:
    """Load services from a JSON or XLSX/XLS file. Returns the number created."""
    path = Path(path)
    rows: list[dict] = []
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("services", data) if isinstance(data, dict) else data
    elif path.suffix.lower() in (".xlsx", ".xls"):
        import pandas as pd

        df = pd.read_excel(path, dtype=str)
        # lower-case AND underscore so "Service Name" -> "service_name" matches lookups
        df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
        rows = df.to_dict(orient="records")
    else:
        raise ValueError(f"unsupported catalogue format: {path.suffix}")

    index = {_key(s.service_name): s for s in session.scalars(select(Service))}
    created = 0
    for r in rows:
        name = (
            r.get("service_name")
            or r.get("name")
            or r.get("услуга")
            or r.get("наименование")
        )
        category = r.get("category") or r.get("категория")
        synonyms = r.get("synonyms") or r.get("синонимы")
        icd = r.get("icd_code") or r.get("icd") or r.get("мкб")
        service_id = r.get("service_id") or r.get("id")
        created += _upsert_service(
            session, index, name, synonyms, category, icd, service_id
        )
    session.flush()  # caller (after rematch) owns the commit -> load+rematch is atomic
    return created


# --------------------------------------------------------------------------- bootstrap
def bootstrap_catalogue(
    session: Session, min_count: int = 2, max_services: int = 4000
) -> int:
    """Synthesize a catalogue from extracted positions when none was provided (TZ 7).

    Groups active items by their normalized name; each group with >= min_count rows
    becomes a Service whose canonical name is the most common raw spelling and whose
    synonyms are the other raw variants. Skips groups already covered by a service.
    """
    items = session.scalars(
        select(PriceItem).where(
            PriceItem.is_active.is_(True), PriceItem.name_norm.isnot(None)
        )
    )
    raws: dict[str, Counter] = defaultdict(Counter)
    sections: dict[str, Counter] = defaultdict(Counter)
    for it in items:
        if not it.name_norm:
            continue
        raws[it.name_norm][it.service_name_raw] += 1
        if it.section:
            sections[it.name_norm][it.section] += 1

    existing_keys = {_key(s.service_name) for s in session.scalars(select(Service))}

    ranked = sorted(raws.items(), key=lambda kv: sum(kv[1].values()), reverse=True)
    created = 0
    for norm_key, raw_counter in ranked:
        if created >= max_services:
            break
        if sum(raw_counter.values()) < min_count or norm_key in existing_keys:
            continue
        variants = [r for r, _ in raw_counter.most_common()]
        canonical = variants[0]
        if _key(canonical) in existing_keys:
            continue
        category = (
            sections[norm_key].most_common(1)[0][0] if sections[norm_key] else None
        )
        session.add(
            Service(
                service_name=canonical,
                synonyms=variants[1:20],
                category=category,
            )
        )
        existing_keys.add(norm_key)
        created += 1
    session.flush()  # caller (after rematch) owns the commit -> bootstrap+rematch is atomic
    return created


# --------------------------------------------------------------------------- rematch
def rematch(session: Session, only_unmatched: bool = True) -> dict:
    """Re-run catalogue matching over price items (TZ 4.3). Returns match stats."""
    matcher = ServiceMatcher(session)
    if matcher.empty:
        session.commit()  # persist any pending catalogue rows from a preceding load/bootstrap
        return {"catalogue_empty": True, "matched": 0, "considered": 0}

    stmt = select(PriceItem).where(PriceItem.is_active.is_(True))
    if only_unmatched:
        stmt = stmt.where(PriceItem.service_id.is_(None))

    considered = matched = 0
    for it in session.scalars(stmt):
        considered += 1
        res = matcher.match(it.service_name_raw)
        if res.service_id is not None:
            it.service_id = res.service_id
            it.match_method = res.method
            it.match_confidence = res.confidence
            matched += 1
        else:
            # no auto-match: keep a fuzzy suggestion score (or clear a now-stale one)
            # so the operator queue ordering stays accurate after a catalogue change
            it.match_confidence = res.confidence
    session.commit()

    total_active = (
        session.scalar(
            select(func.count())
            .select_from(PriceItem)
            .where(PriceItem.is_active.is_(True))
        )
        or 0
    )
    total_matched = (
        session.scalar(
            select(func.count())
            .select_from(PriceItem)
            .where(PriceItem.is_active.is_(True), PriceItem.service_id.isnot(None))
        )
        or 0
    )
    return {
        "considered": considered,
        "newly_matched": matched,
        "total_active": total_active,
        "total_matched": total_matched,
        "match_rate_pct": round(total_matched / total_active * 100, 1)
        if total_active
        else 0.0,
    }
