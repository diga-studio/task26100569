"""性能场景脚本：在子进程中运行，输出 JSON 指标到 stdout。"""
from __future__ import annotations

import json
import os
import random
import resource
import sys
import tempfile
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decay_attr import CHANNELS, AttributionStore, Conversion, Touchpoint

N_TOUCHPOINTS = 1_000_000
N_CONVERSIONS = 100_000
N_DAYS = 90
WHALE_SHARE = 0.8
N_REGULAR_CUSTOMERS = 2000
BATCH = 50_000


def generate(store: AttributionStore) -> None:
    rng = random.Random(20261005)
    base = date(2026, 1, 1)
    whale_tp = int(N_TOUCHPOINTS * WHALE_SHARE)
    whale_cv = int(N_CONVERSIONS * WHALE_SHARE)

    tp_batch: list[Touchpoint] = []
    for i in range(1, N_TOUCHPOINTS + 1):
        if i <= whale_tp:
            customer = "whale"
        else:
            customer = f"cust_{rng.randrange(N_REGULAR_CUSTOMERS)}"
        tp_batch.append(
            Touchpoint(
                id=i,
                customer=customer,
                channel=CHANNELS[rng.randrange(len(CHANNELS))],
                date=base + timedelta(days=rng.randrange(N_DAYS)),
            )
        )
        if len(tp_batch) >= BATCH:
            store.import_touchpoints(tp_batch)
            tp_batch.clear()
    if tp_batch:
        store.import_touchpoints(tp_batch)

    cv_batch: list[Conversion] = []
    for i in range(1, N_CONVERSIONS + 1):
        if i <= whale_cv:
            customer = "whale"
        else:
            customer = f"cust_{rng.randrange(N_REGULAR_CUSTOMERS)}"
        cv_batch.append(
            Conversion(
                id=1_000_000 + i,
                customer=customer,
                date=base + timedelta(days=rng.randrange(N_DAYS)),
                amount=rng.randrange(1, 100_001),
            )
        )
        if len(cv_batch) >= BATCH:
            store.import_conversions(cv_batch)
            cv_batch.clear()
    if cv_batch:
        store.import_conversions(cv_batch)


def main() -> None:
    base = date(2026, 1, 1)
    end = base + timedelta(days=N_DAYS - 1)
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "perf.db")
        with AttributionStore(db_path) as store:
            generate(store)
            start_ts = time.perf_counter()
            run_id = store.run_attribution(base, end)
            elapsed = time.perf_counter() - start_ts

            summary = store.get_run_summary(run_id)
            cur = store._conn.execute("SELECT id, amount FROM conversions")
            amounts = {row[0]: row[1] for row in cur}
            per_conversion_sum = 0
            n_rows = 0
            channel_sum = {c: 0 for c in CHANNELS}
            unattributed_sum = 0
            for result in store.iter_conversion_results(run_id):
                row_total = sum(result.amounts.values())
                assert row_total == amounts[result.conversion_id], "逐笔守恒失败"
                per_conversion_sum += row_total
                n_rows += 1
                for c in CHANNELS:
                    channel_sum[c] += result.amounts[c]
                unattributed_sum += result.amounts["unattributed"]
            assert n_rows == N_CONVERSIONS, "结果未覆盖全部转化"
            for c in CHANNELS:
                assert channel_sum[c] == summary.channel_totals[c], "渠道汇总不一致"
            assert unattributed_sum == summary.unattributed, "未归因汇总不一致"
            total_amount = sum(amounts.values())
            assert per_conversion_sum == total_amount, "整次运行不守恒"

    peak_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
    print(
        json.dumps(
            {
                "elapsed_seconds": round(elapsed, 3),
                "peak_mib": round(peak_mib, 1),
                "conversions": n_rows,
                "total_amount": total_amount,
                "unattributed": summary.unattributed,
            }
        )
    )


if __name__ == "__main__":
    main()
