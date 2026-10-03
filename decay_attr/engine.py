"""时间衰减归因计算引擎。

核心思路：
- 触点按 (客户, 日期, ID) 排序并分组，同组触点权重相同。
- 转化日 D 的窗口为 [D-89, D]，日 t 的触点权重正比于 2^(t-(D-89))，
  用 Python 大整数精确表示，分币时余数比较完全精确。
- 剩余分按 (余数降序, 触点ID升序) 分配；余数相同的跨组触点用归并保证
  严格按 ID 升序，同组内触点已按 ID 排序。
"""
from __future__ import annotations

import heapq

import numpy as np

from .models import LOOKBACK_DAYS

N_CHANNELS = 9
UNATTRIBUTED_COL = N_CHANNELS


def attribute_arrays(
    tp_id: np.ndarray,
    tp_cust: np.ndarray,
    tp_chan: np.ndarray,
    tp_day: np.ndarray,
    cv_id: np.ndarray,
    cv_cust: np.ndarray,
    cv_day: np.ndarray,
    cv_amt: np.ndarray,
) -> np.ndarray:
    """返回 (n_conversions, 10) 的 int64 矩阵，前 9 列为渠道金额，末列为未归因金额。

    行顺序与传入的转化数组顺序一致。同一行的各列之和恒等于该笔转化金额。
    """
    n_cv = len(cv_id)
    results = np.zeros((n_cv, N_CHANNELS + 1), dtype=np.int64)
    if n_cv == 0:
        return results

    n_tp = len(tp_id)
    if n_tp == 0:
        results[:, UNATTRIBUTED_COL] = cv_amt
        return results

    order = np.lexsort((tp_id, tp_day, tp_cust))
    tp_id = tp_id[order]
    tp_chan = tp_chan[order]
    tp_day = tp_day[order]
    tp_cust = tp_cust[order]

    new_group = np.empty(n_tp, dtype=bool)
    new_group[0] = True
    new_group[1:] = (tp_cust[1:] != tp_cust[:-1]) | (tp_day[1:] != tp_day[:-1])
    starts = np.flatnonzero(new_group)
    ends = np.append(starts[1:], n_tp)
    g_cust = tp_cust[starts]
    g_day = tp_day[starts]
    g_n = (ends - starts).astype(np.int64)

    row_group = np.repeat(np.arange(len(starts)), g_n)
    flat = row_group * N_CHANNELS + tp_chan
    g_counts = (
        np.bincount(flat, minlength=len(starts) * N_CHANNELS)
        .reshape(len(starts), N_CHANNELS)
        .astype(np.int32)
    )
    del row_group, flat

    cumsum = np.empty((n_tp, N_CHANNELS), dtype=np.int32)
    for chan in range(N_CHANNELS):
        cumsum[:, chan] = np.cumsum(tp_chan == chan, dtype=np.int32)
    group_base = np.zeros((len(starts), N_CHANNELS), dtype=np.int32)
    nonzero_start = starts > 0
    group_base[nonzero_start] = cumsum[starts[nonzero_start] - 1]

    uniq_cust, first_idx = np.unique(g_cust, return_index=True)
    cust_range: dict[int, tuple[int, int]] = {}
    for pos in range(len(uniq_cust)):
        begin = int(first_idx[pos])
        end = int(first_idx[pos + 1]) if pos + 1 < len(uniq_cust) else len(g_cust)
        cust_range[int(uniq_cust[pos])] = (begin, end)

    g_day_list = g_day.tolist()
    g_n_list = g_n.tolist()
    starts_list = starts.tolist()
    cv_cust_list = cv_cust.tolist()
    cv_day_list = cv_day.tolist()
    cv_amt_list = cv_amt.tolist()

    for row in range(n_cv):
        cust = cv_cust_list[row]
        conv_day = cv_day_list[row]
        amount = cv_amt_list[row]
        span = cust_range.get(cust)
        if span is None:
            results[row, UNATTRIBUTED_COL] = amount
            continue
        g0, g1 = span
        window_start_day = conv_day - LOOKBACK_DAYS
        lo = g0
        hi = g1
        days = g_day_list
        lo = g0 + _lower_bound(days, window_start_day, g0, g1)
        hi = g0 + _lower_bound(days, conv_day + 1, g0, g1)
        k = hi - lo
        if k == 0:
            results[row, UNATTRIBUTED_COL] = amount
            continue

        idx = list(range(lo, hi))
        exps = [days[g] - window_start_day for g in idx]
        counts = [g_n_list[g] for g in idx]

        total_weight = 0
        prev_exp = None
        for pos in range(k - 1, -1, -1):
            exp = exps[pos]
            if prev_exp is None:
                total_weight = counts[pos]
            else:
                total_weight = (total_weight << (prev_exp - exp)) + counts[pos]
            prev_exp = exp
        total_weight <<= exps[0]

        floors: list[int] = [0] * k
        rems: list[int] = [0] * k
        leftover = amount
        for pos in range(k):
            num = amount << exps[pos]
            floor, rem = divmod(num, total_weight)
            floors[pos] = floor
            rems[pos] = rem
            leftover -= floor * counts[pos]

        rank = sorted(range(k), key=lambda p: rems[p], reverse=True)

        vec = np.zeros(N_CHANNELS, dtype=np.int64)
        floor_arr = np.array(floors, dtype=np.int64)
        vec += (floor_arr[:, None] * g_counts[np.array(idx)]).sum(axis=0)

        full_positions: list[int] = []
        partial_group = -1
        partial_take = 0
        slow_channels: list[int] = []
        i = 0
        while leftover > 0 and i < k:
            j = i
            while j + 1 < k and rems[rank[j + 1]] == rems[rank[i]]:
                j += 1
            run = rank[i : j + 1]
            capacity = 0
            for p in run:
                capacity += counts[p]
            if leftover >= capacity:
                full_positions.extend(run)
                leftover -= capacity
            else:
                if len(run) == 1:
                    partial_group = run[0]
                    partial_take = leftover
                else:
                    iterators = []
                    for p in run:
                        s = starts_list[idx[p]]
                        e = s + counts[p]
                        iterators.append(
                            zip(tp_id[s:e].tolist(), tp_chan[s:e].tolist())
                        )
                    taken = 0
                    for _tid, chan in heapq.merge(*iterators):
                        slow_channels.append(chan)
                        taken += 1
                        if taken == leftover:
                            break
                leftover = 0
            i = j + 1

        if full_positions:
            vec += g_counts[np.array(idx)[np.array(full_positions)]].sum(axis=0)
        if partial_group >= 0 and partial_take > 0:
            group_idx = idx[partial_group]
            s = starts_list[group_idx]
            vec += cumsum[s + partial_take - 1] - group_base[group_idx]
        for chan in slow_channels:
            vec[chan] += 1

        results[row, :N_CHANNELS] = vec

    return results


def _lower_bound(values: list[int], target: int, lo: int, hi: int) -> int:
    """在 values[lo:hi]（升序）中找第一个 >= target 的位置，返回相对 lo 的偏移。"""
    origin = lo
    while lo < hi:
        mid = (lo + hi) // 2
        if values[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo - origin
