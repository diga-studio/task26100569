"""Performance test: 1M touchpoints / 100K conversions over 90 days,
with 80% of both belonging to a single customer."""

from __future__ import annotations

import os
import random
import resource
import sys
import tempfile
import time
import unittest

import attribution as A

N_TOUCHPOINTS = 1_000_000
N_CONVERSIONS = 100_000
N_DAYS = 90
SKEW = 0.8
DAY0 = 739000
MAX_SECONDS = 60
MAX_RSS_BYTES = 256 * 1024 * 1024


def generate_and_import(conn):
    rng = random.Random(20261005)
    whale = "customer_whale"
    others = [f"customer_{i}" for i in range(20_000)]
    n_whale_tp = int(N_TOUCHPOINTS * SKEW)
    n_whale_cv = int(N_CONVERSIONS * SKEW)

    batch = []
    for tid in range(1, N_TOUCHPOINTS + 1):
        customer = whale if tid <= n_whale_tp else rng.choice(others)
        batch.append(
            (tid, customer, rng.choice(A.CHANNELS), DAY0 + rng.randrange(N_DAYS))
        )
        if len(batch) >= 100_000:
            A.import_touchpoints(conn, batch)
            batch.clear()
    if batch:
        A.import_touchpoints(conn, batch)
        batch.clear()
    for cid in range(1, N_CONVERSIONS + 1):
        customer = whale if cid <= n_whale_cv else rng.choice(others)
        batch.append(
            (cid, customer, DAY0 + rng.randrange(N_DAYS), rng.randint(1, 100_000))
        )
        if len(batch) >= 100_000:
            A.import_conversions(conn, batch)
            batch.clear()
    if batch:
        A.import_conversions(conn, batch)


def peak_rss_bytes():
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB, macOS reports bytes.
    return rss * 1024 if sys.platform.startswith("linux") else rss


class PerformanceTests(unittest.TestCase):
    def test_scale_run(self):
        tmpdir = tempfile.mkdtemp(prefix="attribution_perf_")
        db_path = os.path.join(tmpdir, "perf.db")
        conn = A.connect(db_path)

        t0 = time.perf_counter()
        generate_and_import(conn)
        import_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        run_id = A.run_attribution(conn, DAY0, DAY0 + N_DAYS - 1)
        run_s = time.perf_counter() - t0
        rss = peak_rss_bytes()

        cols = "+".join(A.CHANNELS) + "+unattributed"
        (n_rows,) = conn.execute(
            "SELECT COUNT(*) FROM conversion_results WHERE run_id = ?", (run_id,)
        ).fetchone()
        (n_bad,) = conn.execute(
            f"SELECT COUNT(*) FROM conversion_results r JOIN conversions c "
            f"ON c.id = r.conversion_id WHERE r.run_id = ? AND ({cols}) != c.amount",
            (run_id,),
        ).fetchone()
        summary = A.get_run_summary(conn, run_id)
        for key, value in summary.items():
            (expected,) = conn.execute(
                f"SELECT COALESCE(SUM({key}), 0) FROM conversion_results WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            self.assertEqual(value, expected, f"summary mismatch for {key}")

        print(
            f"\nimport: {import_s:.1f}s | attribution+save: {run_s:.1f}s | "
            f"peak RSS: {rss / 2**20:.0f} MiB | conversions: {n_rows}"
        )
        self.assertEqual(n_rows, N_CONVERSIONS)
        self.assertEqual(n_bad, 0, "per-conversion conservation violated")
        self.assertLess(run_s, MAX_SECONDS)
        self.assertLess(rss, MAX_RSS_BYTES)
        conn.close()


if __name__ == "__main__":
    unittest.main()
