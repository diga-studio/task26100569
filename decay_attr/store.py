"""SQLite 持久化：导入数据、保存与读取归因结果。"""
from __future__ import annotations

import sqlite3
from array import array
from datetime import date
from typing import Iterable, Iterator

import numpy as np

from .engine import attribute_arrays
from .models import (
    CHANNELS,
    CHANNEL_INDEX,
    UNATTRIBUTED,
    Conversion,
    ConversionResult,
    RunSummary,
    Touchpoint,
)

_RESULT_COLUMNS = ", ".join(CHANNELS + (UNATTRIBUTED,))
_RESULT_PLACEHOLDERS = ", ".join("?" for _ in CHANNELS + (UNATTRIBUTED,))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS touchpoints (
    id INTEGER PRIMARY KEY,
    customer TEXT NOT NULL,
    channel TEXT NOT NULL,
    date TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_touchpoints_customer_date ON touchpoints (customer, date);
CREATE TABLE IF NOT EXISTS conversions (
    id INTEGER PRIMARY KEY,
    customer TEXT NOT NULL,
    date TEXT NOT NULL,
    amount INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversions_date ON conversions (date);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS conversion_results (
    run_id INTEGER NOT NULL,
    conversion_id INTEGER NOT NULL,
    {cols},
    PRIMARY KEY (run_id, conversion_id)
);
CREATE TABLE IF NOT EXISTS run_totals (
    run_id INTEGER PRIMARY KEY,
    {cols}
);
""".replace("{cols}", ", ".join(f"{c} INTEGER NOT NULL" for c in CHANNELS + (UNATTRIBUTED,)))


class AttributionStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=OFF")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AttributionStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def import_touchpoints(self, touchpoints: Iterable[Touchpoint]) -> int:
        rows = [(t.id, t.customer, t.channel, t.date.isoformat()) for t in touchpoints]
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO touchpoints (id, customer, channel, date) VALUES (?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def import_conversions(self, conversions: Iterable[Conversion]) -> int:
        rows = [(c.id, c.customer, c.date.isoformat(), c.amount) for c in conversions]
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO conversions (id, customer, date, amount) VALUES (?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def run_attribution(self, start: date, end: date) -> int:
        """对 [start, end] 内的转化执行归因并保存结果，返回 run_id。"""
        cust_codes: dict[str, int] = {}
        tp = self._load_touchpoint_arrays(cust_codes)
        cv = self._load_conversion_arrays(start, end, cust_codes)
        results = attribute_arrays(*tp, *cv)
        totals = results.sum(axis=0)
        channel_totals = {c: int(totals[i]) for i, c in enumerate(CHANNELS)}
        unattributed = int(totals[len(CHANNELS)])
        cv_ids = cv[0].tolist()

        def _rows() -> Iterator[ConversionResult]:
            for pos, conv_id in enumerate(cv_ids):
                row = results[pos]
                amounts = {c: int(row[i]) for i, c in enumerate(CHANNELS)}
                amounts[UNATTRIBUTED] = int(row[len(CHANNELS)])
                yield ConversionResult(conversion_id=conv_id, amounts=amounts)

        return self.save_run(start, end, _rows(), channel_totals, unattributed)

    def _load_touchpoint_arrays(
        self, cust_codes: dict[str, int]
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        ids = array("q")
        custs = array("i")
        chans = array("B")
        days = array("i")
        cur = self._conn.execute("SELECT id, customer, channel, date FROM touchpoints")
        for row in cur:
            ids.append(row[0])
            code = cust_codes.get(row[1])
            if code is None:
                code = len(cust_codes)
                cust_codes[row[1]] = code
            custs.append(code)
            chans.append(CHANNEL_INDEX[row[2]])
            days.append(date.fromisoformat(row[3]).toordinal())
        return (
            np.frombuffer(ids, dtype=np.int64),
            np.frombuffer(custs, dtype=np.int32),
            np.frombuffer(chans, dtype=np.uint8).astype(np.int64),
            np.frombuffer(days, dtype=np.int32),
        )

    def _load_conversion_arrays(
        self, start: date, end: date, cust_codes: dict[str, int]
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        ids = array("q")
        custs = array("i")
        days = array("i")
        amounts = array("q")
        cur = self._conn.execute(
            "SELECT id, customer, date, amount FROM conversions WHERE date BETWEEN ? AND ? ORDER BY id",
            (start.isoformat(), end.isoformat()),
        )
        for row in cur:
            ids.append(row[0])
            code = cust_codes.get(row[1])
            if code is None:
                code = len(cust_codes)
                cust_codes[row[1]] = code
            custs.append(code)
            days.append(date.fromisoformat(row[2]).toordinal())
            amounts.append(row[3])
        return (
            np.frombuffer(ids, dtype=np.int64),
            np.frombuffer(custs, dtype=np.int32),
            np.frombuffer(days, dtype=np.int32),
            np.frombuffer(amounts, dtype=np.int64),
        )

    def load_touchpoints(self) -> list[Touchpoint]:
        cur = self._conn.execute(
            "SELECT id, customer, channel, date FROM touchpoints ORDER BY id"
        )
        return [
            Touchpoint(id=row[0], customer=row[1], channel=row[2], date=date.fromisoformat(row[3]))
            for row in cur
        ]

    def load_conversions(self, start: date, end: date) -> list[Conversion]:
        cur = self._conn.execute(
            "SELECT id, customer, date, amount FROM conversions WHERE date BETWEEN ? AND ? ORDER BY id",
            (start.isoformat(), end.isoformat()),
        )
        return [
            Conversion(id=row[0], customer=row[1], date=date.fromisoformat(row[2]), amount=row[3])
            for row in cur
        ]

    def save_run(
        self,
        start: date,
        end: date,
        conversion_results: Iterable[ConversionResult],
        channel_totals: dict[str, int],
        unattributed: int,
    ) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO runs (start_date, end_date) VALUES (?, ?)",
                (start.isoformat(), end.isoformat()),
            )
            run_id = int(cur.lastrowid)
            self._conn.executemany(
                f"INSERT INTO conversion_results (run_id, conversion_id, {_RESULT_COLUMNS})"
                f" VALUES (?, ?, {_RESULT_PLACEHOLDERS})",
                (
                    (run_id, r.conversion_id, *(r.amounts.get(c, 0) for c in CHANNELS), r.amounts.get(UNATTRIBUTED, 0))
                    for r in conversion_results
                ),
            )
            self._conn.execute(
                f"INSERT INTO run_totals (run_id, {_RESULT_COLUMNS}) VALUES (?, {_RESULT_PLACEHOLDERS})",
                (run_id, *(channel_totals.get(c, 0) for c in CHANNELS), unattributed),
            )
        return run_id

    def get_run_summary(self, run_id: int) -> RunSummary:
        run = self._conn.execute(
            "SELECT start_date, end_date FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise KeyError(f"run {run_id} not found")
        totals = self._conn.execute(
            f"SELECT {_RESULT_COLUMNS} FROM run_totals WHERE run_id = ?", (run_id,)
        ).fetchone()
        if totals is None:
            raise KeyError(f"run {run_id} totals not found")
        values = dict(zip(CHANNELS + (UNATTRIBUTED,), totals))
        return RunSummary(
            run_id=run_id,
            start_date=date.fromisoformat(run[0]),
            end_date=date.fromisoformat(run[1]),
            channel_totals={c: values[c] for c in CHANNELS},
            unattributed=values[UNATTRIBUTED],
        )

    def iter_conversion_results(self, run_id: int) -> Iterator[ConversionResult]:
        cur = self._conn.execute(
            f"SELECT conversion_id, {_RESULT_COLUMNS} FROM conversion_results"
            " WHERE run_id = ? ORDER BY conversion_id",
            (run_id,),
        )
        for row in cur:
            amounts = dict(zip(CHANNELS + (UNATTRIBUTED,), row[1:]))
            yield ConversionResult(conversion_id=row[0], amounts=amounts)
