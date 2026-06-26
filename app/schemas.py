"""Pydantic response/request models for the API."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    service_id: uuid.UUID
    service_name: str
    category: str | None = None
    synonyms: list[str] = []
    icd_code: str | None = None
    is_active: bool = True


class ServiceIn(BaseModel):
    service_name: str
    category: str | None = None
    synonyms: list[str] = []
    icd_code: str | None = None


class PartnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    partner_id: uuid.UUID
    name: str
    city: str | None = None
    address: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    is_active: bool = True


class PriceItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    item_id: uuid.UUID
    partner_id: uuid.UUID
    doc_id: uuid.UUID
    service_id: uuid.UUID | None = None
    service_name_raw: str
    service_code_source: str | None = None
    section: str | None = None
    unit: str | None = None
    price_resident_kzt: float | None = None
    price_nonresident_kzt: float | None = None
    price_original: float | None = None
    currency_original: str = "KZT"
    match_method: str | None = None
    match_confidence: float | None = None
    needs_review: bool = False
    review_reason: str | None = None
    is_verified: bool = False
    effective_date: date | None = None
    is_active: bool = True


class PartnerPriceOut(BaseModel):
    """A partner together with their price for a given service."""

    partner: PartnerOut
    price_resident_kzt: float | None = None
    price_nonresident_kzt: float | None = None
    currency_original: str = "KZT"
    effective_date: date | None = None
    service_name_raw: str
    item_id: uuid.UUID


class MatchIn(BaseModel):
    item_ids: list[uuid.UUID]
    service_id: uuid.UUID | None = None  # None clears the match
    note: str | None = None


class VerifyIn(BaseModel):
    is_verified: bool = True
    price_resident_kzt: float | None = None
    price_nonresident_kzt: float | None = None
    note: str | None = None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    doc_id: uuid.UUID
    partner_id: uuid.UUID
    file_name: str
    file_format: str
    effective_date: date | None = None
    parsed_at: datetime | None = None
    parse_status: str
    parse_log: str | None = None
