"""正确性测试：与独立的逐触点展开参考实现对比。"""
from __future__ import annotations

import random
from datetime import date, timedelta
from fractions import Fraction

import pytest

from decay_attr import CHANNELS, UNATTRIBUTED, AttributionStore, Conversion, Touchpoint
from decay_attr.models import LOOKBACK_DAYS


def reference_attribute(
    touchpoints: list[Touchpoint], conversion: Conversion
) -> dict[str, int]:
    """独立参考实现：逐触点展开，用 Fraction 保证数学精确。"""
    valid = [
        t
        for t in touchpoints
        if t.customer == conversion.customer
        and 0 <= (conversion.date - t.date).days <= LOOKBACK_DAYS
    ]
    if not valid:
        return {UNATTRIBUTED: conversion.amount}
    weights = {t.id: Fraction(1, 2 ** (conversion.date - t.date).days) for t in valid}
    total = sum(weights.values())
    ideals = {t.id: conversion.amount * weights[t.id] / total for t in valid}
    floors = {t.id: ideals[t.id].numerator // ideals[t.id].denominator for t in valid}
    leftover = conversion.amount - sum(floors.values())
    by_remainder = sorted(
        valid, key=lambda t: (ideals[t.id] - floors[t.id], -t.id), reverse=True
    )
    bonus = {t.id: 0 for t in valid}
    for t in by_remainder[:leftover]:
        bonus[t.id] += 1
    amounts: dict[str, int] = {}
    for t in valid:
        amounts[t.channel] = amounts.get(t.channel, 0) + floors[t.id] + bonus[t.id]
    return amounts


def run_and_collect(
    touchpoints: list[Touchpoint],
    conversions: list[Conversion],
    start: date,
    end: date,
) -> dict[int, dict[str, int]]:
    with AttributionStore(":memory:") as store:
        store.import_touchpoints(touchpoints)
        store.import_conversions(conversions)
        run_id = store.run_attribution(start, end)
        results = {
            r.conversion_id: {k: v for k, v in r.amounts.items() if v}
            for r in store.iter_conversion_results(run_id)
        }
        summary = store.get_run_summary(run_id)
    for conv in conversions:
        if start <= conv.date <= end:
            assert conv.id in results, f"conversion {conv.id} missing"
            got = results[conv.id]
            assert sum(got.values()) == conv.amount, "per-conversion conservation"
    total = sum(
        c.amount for c in conversions if start <= c.date <= end
    )
    assert sum(summary.channel_totals.values()) + summary.unattributed == total
    return results


def test_equal_remainders_broken_by_touchpoint_id() -> None:
    day = date(2026, 3, 10)
    tps = [
        Touchpoint(1, "c1", "paid_search", day),
        Touchpoint(2, "c1", "email", day - timedelta(days=2)),
        Touchpoint(3, "c1", "email", day - timedelta(days=2)),
    ]
    convs = [Conversion(100, "c1", day, 2)]
    results = run_and_collect(tps, convs, day, day)
    assert results[100] == {"paid_search": 2}


def test_window_day_89_included_day_90_excluded() -> None:
    day = date(2026, 3, 10)
    tps = [
        Touchpoint(1, "c1", "email", day - timedelta(days=89)),
        Touchpoint(2, "c1", "social", day - timedelta(days=90)),
    ]
    convs = [Conversion(100, "c1", day, 7)]
    results = run_and_collect(tps, convs, day, day)
    assert results[100] == {"email": 7}


def test_touchpoints_before_run_start_still_used() -> None:
    day = date(2026, 3, 10)
    tps = [Touchpoint(1, "c1", "email", day - timedelta(days=30))]
    convs = [Conversion(100, "c1", day, 5)]
    results = run_and_collect(tps, convs, day, day)
    assert results[100] == {"email": 5}


def test_shared_touchpoints_across_conversions() -> None:
    day = date(2026, 3, 10)
    tps = [Touchpoint(1, "c1", "email", day - timedelta(days=1))]
    convs = [
        Conversion(100, "c1", day, 3),
        Conversion(101, "c1", day + timedelta(days=1), 4),
    ]
    results = run_and_collect(tps, convs, day, day + timedelta(days=1))
    assert results[100] == {"email": 3}
    assert results[101] == {"email": 4}


def test_empty_journey_goes_to_unattributed() -> None:
    day = date(2026, 3, 10)
    tps = [Touchpoint(1, "other", "email", day)]
    convs = [Conversion(100, "c1", day, 9)]
    results = run_and_collect(tps, convs, day, day)
    assert results[100] == {UNATTRIBUTED: 9}


def test_same_channel_same_day_touchpoints_stay_independent() -> None:
    day = date(2026, 3, 10)
    tps = [
        Touchpoint(1, "c1", "email", day),
        Touchpoint(2, "c1", "email", day),
        Touchpoint(3, "c1", "email", day),
    ]
    convs = [Conversion(100, "c1", day, 4)]
    results = run_and_collect(tps, convs, day, day)
    # 每个触点理想 4/3，floor 1，剩 1 补给 id=1 -> 总额不变，但验证未合并成一次触达
    assert results[100] == {"email": 4}
    # 若合并成一次触达，权重不同场景下结果会不同；用不同日期再验证独立性
    tps2 = tps + [Touchpoint(4, "c1", "social", day - timedelta(days=1))]
    convs2 = [Conversion(100, "c1", day, 6)]
    results2 = run_and_collect(tps2, convs2, day, day)
    assert results2[100] == reference_attribute(tps2, convs2[0])


def test_determinism_regardless_of_import_order() -> None:
    rng = random.Random(42)
    base = date(2026, 1, 1)
    tps = [
        Touchpoint(i, f"c{i % 5}", CHANNELS[i % len(CHANNELS)], base + timedelta(days=i % 60))
        for i in range(1, 301)
    ]
    convs = [
        Conversion(1000 + i, f"c{i % 5}", base + timedelta(days=30 + i % 30), 1 + i * 7 % 500)
        for i in range(50)
    ]
    start, end = base + timedelta(days=30), base + timedelta(days=59)
    first = run_and_collect(tps, convs, start, end)
    shuffled_tps = tps[:]
    shuffled_convs = convs[:]
    rng.shuffle(shuffled_tps)
    rng.shuffle(shuffled_convs)
    second = run_and_collect(shuffled_tps, shuffled_convs, start, end)
    assert first == second


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_random_small_against_reference(seed: int) -> None:
    rng = random.Random(seed)
    base = date(2026, 1, 1)
    customers = [f"c{i}" for i in range(6)]
    tps = [
        Touchpoint(
            i,
            rng.choice(customers),
            rng.choice(CHANNELS),
            base + timedelta(days=rng.randrange(120)),
        )
        for i in range(1, 201)
    ]
    convs = [
        Conversion(
            1000 + i,
            rng.choice(customers),
            base + timedelta(days=rng.randrange(90, 120)),
            rng.randrange(1, 1000),
        )
        for i in range(30)
    ]
    start = base + timedelta(days=90)
    end = base + timedelta(days=119)
    results = run_and_collect(tps, convs, start, end)
    for conv in convs:
        expected = reference_attribute(tps, conv)
        expected = {k: v for k, v in expected.items() if v}
        assert results[conv.id] == expected, f"conversion {conv.id} mismatch"
