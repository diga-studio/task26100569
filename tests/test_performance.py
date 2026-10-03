"""性能用例：100 万触点 / 10 万转化 / 90 天 / 80% 集中于同一客户。"""
from __future__ import annotations

import json
import os
import subprocess
import sys

TIME_LIMIT_SECONDS = 60
MEMORY_LIMIT_MIB = 256


def test_performance_at_scale() -> None:
    script = os.path.join(os.path.dirname(__file__), "perf_scenario.py")
    proc = subprocess.run(
        [sys.executable, script],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, f"scenario failed:\n{proc.stdout}\n{proc.stderr}"
    metrics = json.loads(proc.stdout.strip().splitlines()[-1])
    print(f"\n性能指标: {metrics}")
    assert metrics["conversions"] == 100_000
    assert metrics["elapsed_seconds"] < TIME_LIMIT_SECONDS, (
        f"归因+保存耗时 {metrics['elapsed_seconds']}s 超过 {TIME_LIMIT_SECONDS}s"
    )
    assert metrics["peak_mib"] < MEMORY_LIMIT_MIB, (
        f"峰值内存 {metrics['peak_mib']} MiB 超过 {MEMORY_LIMIT_MIB} MiB"
    )
