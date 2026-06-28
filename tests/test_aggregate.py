"""Preview-layer aggregation: exactly-identical name+price rows collapse into one.

Storage keeps every row (a future price-list version may diverge them); only the
/partners/{id}/services preview merges true name+price twins. These tests exercise the
pure helper with in-memory ORM instances — no database required.
"""

from __future__ import annotations

import uuid

from app.api import _aggregate_identical
from app.models import PriceItem


def mk(
    name: str,
    res: float | None = None,
    non: float | None = None,
    code: str | None = None,
    tiers: dict | None = None,
    cur: str = "KZT",
    section: str | None = None,
) -> PriceItem:
    return PriceItem(
        item_id=uuid.uuid4(),
        partner_id=uuid.uuid4(),
        doc_id=uuid.uuid4(),
        service_name_raw=name,
        price_resident_kzt=res,
        price_nonresident_kzt=non,
        service_code_source=code,
        price_extra_tiers=tiers,
        currency_original=cur,
        section=section,
        is_active=True,
        is_verified=False,
        needs_review=False,
    )


def test_identical_name_and_price_merge():
    items = [
        mk("УЗИ щитовидной железы", 5000, code="A1"),
        mk("УЗИ щитовидной железы", 5000, code="A2"),
    ]
    out = _aggregate_identical(items)
    assert len(out) == 1
    assert out[0].merged_count == 2
    assert out[0].merged_codes == ["A1", "A2"]


def test_same_price_different_name_stay_separate():
    # The U2.1.5 / U2.2.3 case: same price, different services -> never fused.
    items = [
        mk("Патронаж медсестры в праздничные и выходные дни", 27700, code="U2.1.5"),
        mk("Осмотр врача в праздничные и выходные дни", 27700, code="U2.2.3"),
    ]
    out = _aggregate_identical(items)
    assert len(out) == 2
    assert all(r.merged_count == 1 for r in out)
    assert all(r.merged_codes is None for r in out)


def test_same_name_different_price_stay_separate():
    items = [mk("Приём терапевта", 9000), mk("Приём терапевта", 7000)]
    out = _aggregate_identical(items)
    assert len(out) == 2


def test_nonresident_and_tiers_are_part_of_the_key():
    # same name + same resident, but the second price field / extra tier differs.
    items = [
        mk("Консультация", 9000, non=7000),
        mk("Консультация", 9000, non=8000),
        mk("Анализ", 1000, tiers={"страховая": 1200}),
        mk("Анализ", 1000, tiers={"страховая": 1500}),
    ]
    out = _aggregate_identical(items)
    assert len(out) == 4  # nothing merges


def test_triplicate_merges_and_preserves_first_position():
    items = [
        mk("ЭКГ", 2000, code="C1", section="Кардиология"),
        mk("Глюкоза", 800, code="L1", section="Лаборатория"),
        mk("ЭКГ", 2000, code="C2", section="Кардиология"),
        mk("ЭКГ", 2000, code="C3", section="Кардиология"),
    ]
    out = _aggregate_identical(items)
    assert [r.service_name_raw for r in out] == ["ЭКГ", "Глюкоза"]
    ecg = out[0]
    assert ecg.merged_count == 3
    assert ecg.merged_codes == ["C1", "C2", "C3"]


def test_currency_is_part_of_the_key():
    items = [mk("Имплант", 1000, cur="USD"), mk("Имплант", 1000, cur="KZT")]
    out = _aggregate_identical(items)
    assert len(out) == 2
