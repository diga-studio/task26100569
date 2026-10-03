"""Import and read APIs backed by SQLite."""

from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Iterable, Sequence

from .schema import CHANNELS, ensure_schema


def connect(path: str) -> sqlite3.Connection:
    """Open (creating if needed) the SQLite database and ensure the schema."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    ensure_schema(conn)
    return conn


def _to_day(value) -> int:
    """Normalize a date-like value to an integer day (proleptic ordinal)."""
    if isinstance(value, int):
        return value
    if isinstance(value, _dt.date):
        return value.toordinal()
    if isinstance(value, str):
        return _dt.date.fromisoformat(value).toordinal()
    raise TypeError(f"unsupported date value: {value!r}")


def import_touchpoints(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[int, str, str, object]],
) -> int:
    """Import touchpoints as (id, customer, channel, day) tuples.

    Returns the number of rows imported.
    """
    data = [(int(tp_id), customer, channel, _to_day(day)) for tp_id, customer, channel, day in rows]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO touchpoints (id, customer, channel, day) VALUES (?, ?, ?, ?)",
            data,
        )
    return len(data)


def import_conversions(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[int, str, object, int]],
) -> int:
    """Import conversions as (id, customer, day, amount_cents) tuples.

    Returns the number of rows imported.
    """
    data = [(int(cv_id), customer, _to_day(day), int(amount)) for cv_id, customer, day, amount in rows]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO conversions (id, customer, day, amount) VALUES (?, ?, ?, ?)",
            data,
        )
    return len(data)


def get_run_summary(conn: sqlite3.Connection, run_id: int) -> dict[str, int]:
    """Return {channel_or_'unattributed': amount_cents} totals for a run."""
    rows = conn.execute(
        "SELECT channel, amount FROM run_channel_totals WHERE run_id = ? ORDER BY channel",
        (run_id,),
    ).fetchall()
    return {channel: amount for channel, amount in rows}


def get_conversion_result(conn: sqlite3.Connection, run_id: int, conversion_id: int) -> dict[str, int]:
    """Return the per-channel amounts for one conversion of a run."""
    columns = ", ".join(CHANNELS) + ", unattributed"
    row = conn.execute(
        f"SELECT {columns} FROM conversion_results WHERE run_id = ? AND conversion_id = ?",
        (run_id, conversion_id),
    ).fetchone()
    if row is None:
        raise KeyError(f"no result for run {run_id}, conversion {conversion_id}")
    return dict(zip((*CHANNELS, "unattributed"), row))


def iter_conversion_results(conn: sqlite3.Connection, run_id: int) -> Iterable[dict[str, int]]:
    """Yield per-conversion results for a run, ordered by conversion id."""
    columns = ", ".join(CHANNELS) + ", unattributed"
    cursor = conn.execute(
        f"SELECT conversion_id, {columns} FROM conversion_results WHERE run_id = ? ORDER BY conversion_id",
        (run_id,),
    )
    keys = ("conversion_id", *CHANNELS, "unattributed")
    for row in cursor:
        yield dict(zip(keys, row))
