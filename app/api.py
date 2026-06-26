"""FastAPI application implementing the TZ 4.5 search API plus upload / review endpoints.

Run: uvicorn app.api:app --reload
Docs: /docs (Swagger) and /openapi.json
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .db import get_session, init_db
from .models import Partner, PriceDocument, PriceItem, Service
from .ocr_api import router as ocr_router
from .pipeline import ingest_file
from .schemas import (
    DocumentOut,
    MatchIn,
    PartnerOut,
    PartnerPriceOut,
    PriceItemOut,
    ServiceIn,
    ServiceOut,
    VerifyIn,
)

app = FastAPI(
    title="MedArchive API",
    version="1.0",
    description="Partner clinics, normalized services and prices extracted from price-list archives.",
)

STATIC_DIR = Path(__file__).parent / "static"


app.include_router(ocr_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()


# ----------------------------------------------------------------------------- services
@app.get("/services", response_model=list[ServiceOut], tags=["services"])
def list_services(
    category: str | None = None,
    q: str | None = None,
    limit: int = Query(100, le=1000),
    offset: int = 0,
    db: Session = Depends(get_session),
):
    stmt = select(Service).where(Service.is_active.is_(True))
    if category:
        stmt = stmt.where(Service.category == category)
    if q:
        stmt = stmt.where(Service.service_name.ilike(f"%{q}%"))
    stmt = stmt.order_by(Service.service_name).limit(limit).offset(offset)
    return db.scalars(stmt).all()


@app.post("/services", response_model=ServiceOut, tags=["services"], status_code=201)
def create_service(body: ServiceIn, db: Session = Depends(get_session)):
    svc = Service(
        service_name=body.service_name,
        category=body.category,
        synonyms=body.synonyms,
        icd_code=body.icd_code,
    )
    db.add(svc)
    db.commit()
    return svc


@app.get(
    "/services/{service_id}/partners",
    response_model=list[PartnerPriceOut],
    tags=["services"],
)
def service_partners(service_id: uuid.UUID, db: Session = Depends(get_session)):
    if not db.get(Service, service_id):
        raise HTTPException(404, "service not found")
    rows = db.scalars(
        select(PriceItem)
        .where(PriceItem.service_id == service_id, PriceItem.is_active.is_(True))
        .order_by(PriceItem.price_resident_kzt.asc().nulls_last())
    ).all()
    return [_partner_price(db, it) for it in rows]


# ----------------------------------------------------------------------------- partners
@app.get("/partners", response_model=list[PartnerOut], tags=["partners"])
def list_partners(
    city: str | None = None,
    is_active: bool | None = None,
    limit: int = Query(100, le=1000),
    offset: int = 0,
    db: Session = Depends(get_session),
):
    stmt = select(Partner)
    if city:
        stmt = stmt.where(Partner.city.ilike(f"%{city}%"))
    if is_active is not None:
        stmt = stmt.where(Partner.is_active.is_(is_active))
    stmt = stmt.order_by(Partner.name).limit(limit).offset(offset)
    return db.scalars(stmt).all()


@app.get(
    "/partners/{partner_id}/services",
    response_model=list[PriceItemOut],
    tags=["partners"],
)
def partner_services(
    partner_id: uuid.UUID,
    active_only: bool = True,
    limit: int = Query(2000, le=10000),
    offset: int = 0,
    db: Session = Depends(get_session),
):
    if not db.get(Partner, partner_id):
        raise HTTPException(404, "partner not found")
    stmt = select(PriceItem).where(PriceItem.partner_id == partner_id)
    if active_only:
        stmt = stmt.where(PriceItem.is_active.is_(True))
    stmt = (
        stmt.order_by(PriceItem.section, PriceItem.service_name_raw)
        .limit(limit)
        .offset(offset)
    )
    return db.scalars(stmt).all()


# ----------------------------------------------------------------------------- search
@app.get("/search", tags=["search"])
def search(
    q: str = Query(..., min_length=2),
    limit: int = Query(50, le=500),
    db: Session = Depends(get_session),
):
    like = f"%{q}%"
    partners = db.scalars(
        select(Partner).where(Partner.name.ilike(like)).limit(limit)
    ).all()
    services = db.scalars(
        select(Service)
        .where(Service.service_name.ilike(like), Service.is_active.is_(True))
        .limit(limit)
    ).all()
    items = db.scalars(
        select(PriceItem)
        .where(PriceItem.service_name_raw.ilike(like), PriceItem.is_active.is_(True))
        .limit(limit)
    ).all()
    return {
        "partners": [PartnerOut.model_validate(p).model_dump() for p in partners],
        "services": [ServiceOut.model_validate(s).model_dump() for s in services],
        "price_items": [PriceItemOut.model_validate(i).model_dump() for i in items],
    }


# ----------------------------------------------------------------- operator / review (TZ 4.3-4.4)
@app.get("/unmatched", response_model=list[PriceItemOut], tags=["operator"])
def unmatched(
    limit: int = Query(200, le=2000),
    offset: int = 0,
    db: Session = Depends(get_session),
):
    stmt = (
        select(PriceItem)
        .where(PriceItem.service_id.is_(None), PriceItem.is_active.is_(True))
        .order_by(PriceItem.match_confidence.desc().nulls_last())
        .limit(limit)
        .offset(offset)
    )
    return db.scalars(stmt).all()


@app.get("/review", response_model=list[PriceItemOut], tags=["operator"])
def review_queue(
    limit: int = Query(200, le=2000),
    offset: int = 0,
    db: Session = Depends(get_session),
):
    stmt = (
        select(PriceItem)
        .where(PriceItem.needs_review.is_(True), PriceItem.is_verified.is_(False))
        .order_by(PriceItem.effective_date.desc().nulls_last())
        .limit(limit)
        .offset(offset)
    )
    return db.scalars(stmt).all()


@app.post("/match", response_model=list[PriceItemOut], tags=["operator"])
def match(body: MatchIn, db: Session = Depends(get_session)):
    if body.service_id is not None and not db.get(Service, body.service_id):
        raise HTTPException(404, "service not found")
    items = db.scalars(
        select(PriceItem).where(PriceItem.item_id.in_(body.item_ids))
    ).all()
    if not items:
        raise HTTPException(404, "no matching items")
    for it in items:
        it.service_id = body.service_id
        it.match_method = "manual" if body.service_id else None
        it.match_confidence = 1.0 if body.service_id else None
        if body.note:
            it.verification_note = body.note
    db.commit()
    return items


@app.post("/items/{item_id}/verify", response_model=PriceItemOut, tags=["operator"])
def verify_item(item_id: uuid.UUID, body: VerifyIn, db: Session = Depends(get_session)):
    it = db.get(PriceItem, item_id)
    if not it:
        raise HTTPException(404, "item not found")
    it.is_verified = body.is_verified
    if body.is_verified:
        it.needs_review = False
    if body.price_resident_kzt is not None:
        it.price_resident_kzt = body.price_resident_kzt
    if body.price_nonresident_kzt is not None:
        it.price_nonresident_kzt = body.price_nonresident_kzt
    if body.note:
        it.verification_note = body.note
    db.commit()
    return it


# ----------------------------------------------------------------------------- documents / ingest
@app.get("/documents", response_model=list[DocumentOut], tags=["documents"])
def list_documents(
    status: str | None = None,
    limit: int = Query(200, le=2000),
    db: Session = Depends(get_session),
):
    stmt = select(PriceDocument).order_by(PriceDocument.parsed_at.desc())
    if status:
        stmt = stmt.where(PriceDocument.parse_status == status)
    return db.scalars(stmt.limit(limit)).all()


@app.post("/upload", tags=["documents"])
async def upload(
    file: UploadFile = File(...),
    force: bool = False,
    db: Session = Depends(get_session),
):
    name = file.filename or "upload"
    suffix = Path(name).suffix.lower()
    if suffix not in {".pdf", ".docx", ".xlsx", ".xls"}:
        raise HTTPException(415, f"unsupported file type: {suffix}")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        report = ingest_file(db, tmp_path, original_name=name, force=force)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(422, f"parse failed: {type(e).__name__}: {e}") from e
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return report.as_dict()


@app.get("/stats", tags=["dashboard"])
def stats(db: Session = Depends(get_session)):
    items = db.scalar(select(func.count()).select_from(PriceItem)) or 0
    matched = (
        db.scalar(
            select(func.count())
            .select_from(PriceItem)
            .where(PriceItem.service_id.isnot(None))
        )
        or 0
    )
    review = (
        db.scalar(
            select(func.count())
            .select_from(PriceItem)
            .where(PriceItem.needs_review.is_(True))
        )
        or 0
    )
    by_status = dict(
        db.execute(
            select(PriceDocument.parse_status, func.count()).group_by(
                PriceDocument.parse_status
            )
        ).all()
    )
    return {
        "documents": db.scalar(select(func.count()).select_from(PriceDocument)) or 0,
        "documents_by_status": by_status,
        "partners": db.scalar(select(func.count()).select_from(Partner)) or 0,
        "services": db.scalar(select(func.count()).select_from(Service)) or 0,
        "price_items": items,
        "auto_matched": matched,
        "match_rate_pct": round(matched / items * 100, 1) if items else 0.0,
        "needs_review": review,
    }


def _partner_price(db: Session, it: PriceItem) -> PartnerPriceOut:
    return PartnerPriceOut(
        partner=PartnerOut.model_validate(db.get(Partner, it.partner_id)),
        price_resident_kzt=float(it.price_resident_kzt)
        if it.price_resident_kzt is not None
        else None,
        price_nonresident_kzt=float(it.price_nonresident_kzt)
        if it.price_nonresident_kzt is not None
        else None,
        currency_original=it.currency_original,
        effective_date=it.effective_date,
        service_name_raw=it.service_name_raw,
        item_id=it.item_id,
    )


# Static upload UI at "/".
if STATIC_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")


@app.get("/", include_in_schema=False)
def index():
    idx = STATIC_DIR / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return {"service": "MedArchive", "docs": "/docs"}
