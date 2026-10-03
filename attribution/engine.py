"""Time-decay attribution engine."""

from __future__ import annotations

import datetime as _dt
import sqlite3
from itertools import groupby
from operator import itemgetter

import numpy as np

from .schema import CHANNELS, CHANNEL_CODE, WINDOW_DAYS
from .store import _to_day

_LOOKBACK = WINDOW_DAYS - 1  # conversion day plus 89 prior days


def run_attribution(conn: sqlite3.Connection, start_day, end_day) -> int:
    """Run attribution over conversions with start_day <= day <= end_day.

    Both bounds are inclusive and may be ints (ordinals), datetime.date or
    ISO date strings. Only conversions are filtered by the range; the
    historical touchpoints they need are not truncated.

    Persists per-conversion channel amounts and run-level channel totals,
    and returns the new run id.
    """
    start = _to_day(start_day)
    end = _to_day(end_day)

    conv_cursor = conn.execute(
        "SELECT id, customer, day, amount FROM conversions "
        "WHERE day BETWEEN ? AND ? ORDER BY customer, day, id",
        (start, end),
    )
    tp_cursor = conn.execute(
        "SELECT customer, day, id, channel FROM touchpoints ORDER BY customer, day, id"
    )

    with conn:
        run_id = conn.execute(
            "INSERT INTO runs (start_day, end_day, created_at) VALUES (?, ?, ?)",
            (start, end, _dt.datetime.now(_dt.timezone.utc).isoformat()),
        ).lastrowid

        totals = {name: 0 for name in (*CHANNELS, "unattributed")}
        tp_iter = iter(tp_cursor)
        tp_row = next(tp_iter, None)

        for customer, conv_group in groupby(conv_cursor, key=itemgetter(1)):
            convs = [(row[0], row[2], row[3]) for row in conv_group]
            while tp_row is not None and tp_row[0] < customer:
                tp_row = next(tp_iter, None)
            if tp_row is not None and tp_row[0] == customer:
                ids: list[int] = []
                days: list[int] = []
                channels: list[int] = []
                while tp_row is not None and tp_row[0] == customer:
                    ids.append(tp_row[2])
                    days.append(tp_row[1])
                    channels.append(CHANNEL_CODE[tp_row[3]])
                    tp_row = next(tp_iter, None)
                tp_data = (ids, days, channels)
            else:
                tp_data = None
            results = _attribute_customer(tp_data, convs)
            conn.executemany(
                "INSERT INTO conversion_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ((run_id, cid, *amounts) for cid, amounts in results),
            )
            for cid, amounts in results:
                for idx, value in enumerate(amounts):
                    totals[CHANNELS[idx] if idx < len(CHANNELS) else "unattributed"] += value

        conn.executemany(
            "INSERT INTO run_channel_totals (run_id, channel, amount) VALUES (?, ?, ?)",
            ((run_id, name, amount) for name, amount in totals.items()),
        )
    return run_id


def _attribute_customer(tp_data, convs):
    """Attribute one customer's conversions.

    tp_data is (ids, days, channels) sorted by (day, id), or None.
    convs is a list of (conversion_id, day, amount) sorted by (day, id).
    Returns [(conversion_id, [9 channel amounts..., unattributed]), ...].
    """
    if tp_data is not None:
        ids = np.asarray(tp_data[0], dtype=np.int64)
        days = np.asarray(tp_data[1], dtype=np.int64)
        tps_ch = np.asarray(tp_data[2], dtype=np.int64)
    else:
        ids = days = tps_ch = None

    base = min([day for _, day, _ in convs] + ([int(days[0])] if days is not None and len(days) else []))
    max_conv_off = max(day - base for _, day, _ in convs)

    if days is not None and len(days):
        day_off = days - base
        n_off = max(int(day_off[-1]), max_conv_off) + 1
        cnt = np.bincount(day_off, minlength=n_off)
        mat = np.zeros((n_off, len(CHANNELS)), dtype=np.int64)
        np.add.at(mat, (day_off, tps_ch), 1)
        day_start = np.zeros(n_off, dtype=np.int64)
        np.cumsum(cnt[:-1], out=day_start[1:])
        # Exact integer prefix sums of cnt[off] * 2**off.
        p2 = [0] * (n_off + 1)
        acc = 0
        for off in range(n_off):
            acc += int(cnt[off]) << off
            p2[off + 1] = acc
    else:
        cnt = mat = day_start = p2 = None
        n_off = 0

    results = []
    for conv_id, day, amount in convs:
        results.append((conv_id, _attribute_one(day - base, amount, cnt, mat, day_start, p2, ids, tps_ch)))
    return results


def _attribute_one(tb, amount, cnt, mat, day_start, p2, ids, tps_ch):
    """Attribute a single conversion at day-offset tb with the given amount.

    Returns a list of 10 integer amounts: 9 channels + unattributed.
    """
    out = [0] * (len(CHANNELS) + 1)
    if cnt is None:
        out[-1] = amount
        return out

    lo = tb - _LOOKBACK
    diff = p2[tb + 1] - (p2[lo] if lo > 0 else 0)
    total_weight = (diff >> (tb - _LOOKBACK)) if tb >= _LOOKBACK else (diff << (_LOOKBACK - tb))
    if total_weight == 0:
        out[-1] = amount
        return out

    start = max(0, lo)
    value = amount << (_LOOKBACK - tb + start)
    qs = []
    remainders = []  # (remainder, day_offset) for days that have touchpoints
    for off in range(start, tb + 1):
        if cnt[off]:
            q, r = divmod(value, total_weight)
            qs.append(q)
            remainders.append((r, off))
        else:
            qs.append(0)
        value <<= 1

    window = mat[start : tb + 1]
    floors = window.T @ np.asarray(qs, dtype=np.int64)
    leftover = amount - int(floors.sum())

    if leftover:
        remainders.sort(key=lambda item: -item[0])
        idx = 0
        n = len(remainders)
        while leftover and idx < n:
            rem = remainders[idx][0]
            group_offs = []
            while idx < n and remainders[idx][0] == rem:
                group_offs.append(remainders[idx][1])
                idx += 1
            group_count = sum(int(cnt[off]) for off in group_offs)
            if leftover >= group_count:
                floors += mat[np.asarray(group_offs)].sum(axis=0)
                leftover -= group_count
            else:
                slices = [
                    (int(day_start[off]), int(day_start[off]) + int(cnt[off]))
                    for off in group_offs
                ]
                if len(slices) == 1:
                    s, e = slices[0]
                    group_ids = ids[s:e]
                    group_ch = tps_ch[s:e]
                else:
                    group_ids = np.concatenate([ids[s:e] for s, e in slices])
                    group_ch = np.concatenate([tps_ch[s:e] for s, e in slices])
                order = np.argsort(group_ids, kind="stable")
                chosen = group_ch[order[:leftover]]
                floors += np.bincount(chosen, minlength=len(CHANNELS))
                leftover = 0

    for ch_idx in range(len(CHANNELS)):
        out[ch_idx] = int(floors[ch_idx])
    return out
