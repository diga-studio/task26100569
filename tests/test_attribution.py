"""Correctness tests: exact scenarios plus a brute-force Fraction reference."""

from __future__ import annotations

import random
import unittest
from fractions import Fraction

import attribution as A

DAY0 = 739000  # arbitrary ordinal base


def reference_results(touchpoints, conversions, start, end):
    """Independent per-touchpoint exact computation using Fraction.

    touchpoints: [(id, customer, channel, day)]
    conversions: [(id, customer, day, amount)]
    Returns {conversion_id: {channel_or_'unattributed': amount}}.
    """
    results = {}
    for cid, cust, cday, amount in conversions:
        if not (start <= cday <= end):
            continue
        tps = [
            (tid, ch, cday - d)
            for tid, cu, ch, d in touchpoints
            if cu == cust and 0 <= cday - d <= 89
        ]
        out = {name: 0 for name in (*A.CHANNELS, "unattributed")}
        if not tps:
            out["unattributed"] = amount
            results[cid] = out
            continue
        weights = [Fraction(1, 2 ** age) for _, _, age in tps]
        total = sum(weights)
        ideals = [amount * w / total for w in weights]
        floors = [ideal.numerator // ideal.denominator for ideal in ideals]
        leftover = amount - sum(floors)
        order = sorted(
            range(len(tps)),
            key=lambda i: (-(ideals[i] - floors[i]), tps[i][0]),
        )
        for i in order[:leftover]:
            floors[i] += 1
        for (tid, ch, _), value in zip(tps, floors):
            out[ch] += value
        results[cid] = out
    return results


def run_library(touchpoints, conversions, start, end):
    conn = A.connect(":memory:")
    A.import_touchpoints(conn, touchpoints)
    A.import_conversions(conn, conversions)
    run_id = A.run_attribution(conn, start, end)
    per_conv = {
        row["conversion_id"]: {k: v for k, v in row.items() if k != "conversion_id"}
        for row in A.iter_conversion_results(conn, run_id)
    }
    return run_id, per_conv, A.get_run_summary(conn, run_id)


class ScenarioTests(unittest.TestCase):
    def test_equal_fractional_parts_tie_break_by_id(self):
        # 2-cent conversion; id 1 paid_search same day, ids 2/3 email two days earlier.
        touchpoints = [
            (1, "c1", "paid_search", DAY0 + 2),
            (2, "c1", "email", DAY0),
            (3, "c1", "email", DAY0),
        ]
        conversions = [(100, "c1", DAY0 + 2, 2)]
        _, per_conv, summary = run_library(touchpoints, conversions, DAY0 + 2, DAY0 + 2)
        self.assertEqual(per_conv[100]["paid_search"], 2)
        self.assertEqual(per_conv[100]["email"], 0)
        self.assertEqual(per_conv[100]["unattributed"], 0)
        self.assertEqual(summary["paid_search"], 2)

    def test_window_day_89_included_day_90_excluded(self):
        touchpoints = [
            (1, "c1", "email", DAY0),        # 90 days before: excluded
            (2, "c1", "social", DAY0 + 1),   # 89 days before: included
        ]
        conversions = [(100, "c1", DAY0 + 90, 3)]
        _, per_conv, _ = run_library(touchpoints, conversions, DAY0 + 90, DAY0 + 90)
        self.assertEqual(per_conv[100]["social"], 3)
        self.assertEqual(per_conv[100]["email"], 0)
        self.assertEqual(per_conv[100]["unattributed"], 0)

    def test_touchpoints_before_run_start_still_count(self):
        # Run range only filters conversions, not the history they use.
        touchpoints = [(1, "c1", "webinar", DAY0)]
        conversions = [(100, "c1", DAY0 + 10, 7)]
        _, per_conv, _ = run_library(touchpoints, conversions, DAY0 + 10, DAY0 + 10)
        self.assertEqual(per_conv[100]["webinar"], 7)

    def test_multiple_conversions_share_touchpoints(self):
        touchpoints = [(1, "c1", "display_ad", DAY0)]
        conversions = [
            (100, "c1", DAY0, 5),
            (101, "c1", DAY0 + 1, 9),
        ]
        _, per_conv, summary = run_library(touchpoints, conversions, DAY0, DAY0 + 1)
        self.assertEqual(per_conv[100]["display_ad"], 5)
        self.assertEqual(per_conv[101]["display_ad"], 9)
        self.assertEqual(summary["display_ad"], 14)

    def test_empty_journey_is_unattributed_not_direct(self):
        touchpoints = [(1, "c1", "direct", DAY0)]
        conversions = [
            (100, "c1", DAY0, 4),   # has a touchpoint
            (101, "c2", DAY0, 6),   # no touchpoints at all
        ]
        _, per_conv, summary = run_library(touchpoints, conversions, DAY0, DAY0)
        self.assertEqual(per_conv[100]["direct"], 4)
        self.assertEqual(per_conv[101]["unattributed"], 6)
        self.assertEqual(per_conv[101]["direct"], 0)
        self.assertEqual(summary["unattributed"], 6)

    def test_run_range_filters_conversions_only(self):
        touchpoints = [(1, "c1", "email", DAY0)]
        conversions = [
            (100, "c1", DAY0, 4),       # outside run range
            (101, "c1", DAY0 + 5, 8),   # inside
        ]
        _, per_conv, _ = run_library(touchpoints, conversions, DAY0 + 5, DAY0 + 5)
        self.assertNotIn(100, per_conv)
        self.assertEqual(per_conv[101]["email"], 8)


class ReferenceComparisonTests(unittest.TestCase):
    def test_random_small_against_reference(self):
        rng = random.Random(20261005)
        for trial in range(30):
            customers = [f"c{i}" for i in range(rng.randint(1, 4))]
            touchpoints = [
                (
                    tid,
                    rng.choice(customers),
                    rng.choice(A.CHANNELS),
                    DAY0 + rng.randint(0, 100),
                )
                for tid in range(1, rng.randint(2, 40))
            ]
            conversions = [
                (
                    1000 + cid,
                    rng.choice(customers),
                    DAY0 + rng.randint(20, 110),
                    rng.randint(1, 500),
                )
                for cid in range(rng.randint(1, 15))
            ]
            start, end = DAY0 + 20, DAY0 + 110
            expected = reference_results(touchpoints, conversions, start, end)
            _, per_conv, summary = run_library(touchpoints, conversions, start, end)
            self.assertEqual(per_conv, expected, f"trial {trial}")
            # Conservation: every conversion sums to its amount.
            amounts = {cid: amt for cid, _, _, amt in conversions}
            for cid, out in per_conv.items():
                self.assertEqual(sum(out.values()), amounts[cid])
            # Run summary equals the sum of per-conversion rows.
            for key, value in summary.items():
                self.assertEqual(sum(out[key] for out in per_conv.values()), value)

    def test_import_order_does_not_matter(self):
        rng = random.Random(7)
        customers = [f"c{i}" for i in range(3)]
        touchpoints = [
            (tid, rng.choice(customers), rng.choice(A.CHANNELS), DAY0 + rng.randint(0, 60))
            for tid in range(1, 60)
        ]
        conversions = [
            (1000 + cid, rng.choice(customers), DAY0 + rng.randint(10, 60), rng.randint(1, 200))
            for cid in range(20)
        ]
        _, first, _ = run_library(touchpoints, conversions, DAY0, DAY0 + 60)
        shuffled_tp = touchpoints[:]
        shuffled_cv = conversions[:]
        rng.shuffle(shuffled_tp)
        rng.shuffle(shuffled_cv)
        _, second, _ = run_library(shuffled_tp, shuffled_cv, DAY0, DAY0 + 60)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
