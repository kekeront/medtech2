"""Database schema — mirrors TZ section 3 (Partner / PriceDocument / PriceItem / Service).

Enums are emulated with VARCHAR + CHECK (native_enum=False) to keep migrations painless.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

FILE_FORMATS = ("pdf", "docx", "xlsx", "xls", "scan_pdf")
PARSE_STATUSES = ("pending", "processing", "done", "error", "needs_review")
CURRENCIES = ("KZT", "USD", "RUB")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Partner(Base):
    __tablename__ = "partners"

    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    city: Mapped[str | None] = mapped_column(String(128))
    address: Mapped[str | None] = mapped_column(String(512))
    bin: Mapped[str | None] = mapped_column(String(12), index=True)  # for dedup
    contact_email: Mapped[str | None] = mapped_column(String(256))
    contact_phone: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    documents: Mapped[list["PriceDocument"]] = relationship(back_populates="partner")

    __table_args__ = (UniqueConstraint("name", name="uq_partner_name"),)


class PriceDocument(Base):
    __tablename__ = "price_documents"

    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    partner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partners.partner_id"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_format: Mapped[str] = mapped_column(String(16), nullable=False)
    effective_date: Mapped[date | None] = mapped_column(Date)
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    parse_status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False
    )
    parse_log: Mapped[str | None] = mapped_column(Text)
    raw_content: Mapped[str | None] = mapped_column(Text)  # extracted text, for audit
    content_sha256: Mapped[str | None] = mapped_column(
        String(64), index=True
    )  # re-ingest dedup

    partner: Mapped["Partner"] = relationship(back_populates="documents")
    items: Mapped[list["PriceItem"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(f"file_format IN {FILE_FORMATS}", name="ck_doc_format"),
        CheckConstraint(f"parse_status IN {PARSE_STATUSES}", name="ck_doc_status"),
    )


class PriceItem(Base):
    __tablename__ = "price_items"

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("price_documents.doc_id"), nullable=False
    )
    partner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partners.partner_id"), nullable=False, index=True
    )  # denormalized for query speed (TZ 3.3)

    service_name_raw: Mapped[str] = mapped_column(Text, nullable=False)
    # Russian-normalized form (homoglyphs folded, abbreviations expanded) for robust search.
    name_norm: Mapped[str | None] = mapped_column(Text, index=True)
    service_code_source: Mapped[str | None] = mapped_column(String(64))
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("services.service_id"), index=True
    )

    price_resident_kzt: Mapped[float | None] = mapped_column(Numeric(14, 2))
    price_nonresident_kzt: Mapped[float | None] = mapped_column(Numeric(14, 2))
    # Any priced tier beyond resident/nonresident (e.g. дальнее зарубежье, страховая,
    # партнёрская скидка) — {label: kzt_value}. Keeps 3+-tier price lists lossless.
    price_extra_tiers: Mapped[dict | None] = mapped_column(JSONB)
    price_original: Mapped[float | None] = mapped_column(Numeric(14, 2))
    currency_original: Mapped[str] = mapped_column(
        String(8), default="KZT", nullable=False
    )

    # normalization metadata
    match_method: Mapped[str | None] = mapped_column(
        String(32)
    )  # exact / synonym / fuzzy / manual
    match_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))

    # verification (TZ 4.4)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_note: Mapped[str | None] = mapped_column(Text)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_reason: Mapped[str | None] = mapped_column(Text)

    # audit / context
    section: Mapped[str | None] = mapped_column(
        String(512)
    )  # section header in the source doc
    unit: Mapped[str | None] = mapped_column(String(128))
    source_row: Mapped[str | None] = mapped_column(Text)  # raw row text as extracted

    effective_date: Mapped[date | None] = mapped_column(Date, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    document: Mapped["PriceDocument"] = relationship(back_populates="items")
    service: Mapped["Service | None"] = relationship(back_populates="items")

    __table_args__ = (
        CheckConstraint(f"currency_original IN {CURRENCIES}", name="ck_item_currency"),
        Index("ix_item_partner_active", "partner_id", "is_active"),
        Index("ix_item_service_active", "service_id", "is_active"),
    )


class Service(Base):
    """Target reference catalogue (TZ 3.4). Loaded from organizers' XLSX/JSON."""

    __tablename__ = "services"

    service_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    service_name: Mapped[str] = mapped_column(String(512), nullable=False)
    synonyms: Mapped[list] = mapped_column(JSONB, default=list)
    category: Mapped[str | None] = mapped_column(String(128), index=True)
    icd_code: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    items: Mapped[list["PriceItem"]] = relationship(back_populates="service")
