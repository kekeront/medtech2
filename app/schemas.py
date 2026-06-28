"""Pydantic response/request models for the API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PatchItemIn(BaseModel):
    service_name_raw: str | None = None
    section: str | None = None
    price_resident_kzt: float | None = None
    price_nonresident_kzt: float | None = None
    effective_date: date | None = None


class NewItemIn(BaseModel):
    service_name_raw: str
    section: str | None = None
    price_resident_kzt: float | None = None
    price_nonresident_kzt: float | None = None
    effective_date: date | None = None


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
    partner_name: str | None = None  # enriched in list endpoints for operator context
    doc_id: uuid.UUID
    service_id: uuid.UUID | None = None
    service_name_raw: str
    service_code_source: str | None = None
    section: str | None = None
    unit: str | None = None
    price_resident_kzt: float | None = None
    price_nonresident_kzt: float | None = None
    price_extra_tiers: dict[str, float] | None = None
    price_original: float | None = None
    currency_original: str = "KZT"
    match_method: str | None = None
    match_confidence: float | None = None
    needs_review: bool = False
    review_reason: str | None = None
    is_verified: bool = False
    effective_date: date | None = None
    is_active: bool = True
    # Preview-only aggregation: how many storage rows this displayed row stands for, and
    # the source codes of the merged siblings. >1 only when the preview collapsed rows that
    # were EXACTLY identical in name + price; storage still keeps each row separately.
    merged_count: int = 1
    merged_codes: list[str] | None = None


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


# --------------------------------------------------------------------------- fx rates


class FxRate(BaseModel):
    """KZT exchange rate for one foreign-currency unit.

    Direction: per_unit_kzt = how many KZT you get for 1 unit of the currency.
    Example: per_unit_kzt=486.47 means 1 USD = 486.47 KZT.

    raw    — the <description> value from the NBK feed, quoted per <quant> units.
    quant  — the <quant> field from the feed (often 1, but e.g. AMD uses 10).
    per_unit_kzt = raw / quant  (never assume quant == 1).
    """

    per_unit_kzt: float
    quant: int = 1
    raw: float


class FxRatesOut(BaseModel):
    """Live USD->KZT and RUB->KZT exchange rates from the National Bank of Kazakhstan."""

    base: str = "KZT"
    as_of: str | None = None  # DD.MM.YYYY from the NBK feed; None on static fallback
    # nbk = live feed; last_good = last successful NBK rate (NBK currently down);
    # fallback = static config defaults (NBK never reached).
    source: Literal["nbk", "last_good", "fallback"]
    stale: bool = False  # True when a non-live (last_good / static) value is served
    rates: dict[str, FxRate]  # keys: "USD", "RUB"
    error: str | None = None  # human-readable reason when source != "nbk"
