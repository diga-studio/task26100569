"""SQLite schema and shared constants for the attribution library."""

from __future__ import annotations

#: Fixed channel vocabulary, in a stable canonical order.
CHANNELS: tuple[str, ...] = (
    "paid_search",
    "organic_search",
    "email",
    "social",
    "direct",
    "referral",
    "display_ad",
    "webinar",
    "content",
)

#: Maps channel name -> small integer code used inside the engine.
CHANNEL_CODE: dict[str, int] = {name: idx for idx, name in enumerate(CHANNELS)}

#: Attribution lookback window in days (conversion day + 89 prior days).
WINDOW_DAYS: int = 90

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS touchpoints (
    id       INTEGER PRIMARY KEY,
    customer TEXT NOT NULL,
    channel  TEXT NOT NULL,
    day      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS conversions (
    id       INTEGER PRIMARY KEY,
    customer TEXT NOT NULL,
    day      INTEGER NOT NULL,
    amount   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY,
    start_day  INTEGER NOT NULL,
    end_day    INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_channel_totals (
    run_id  INTEGER NOT NULL REFERENCES runs(id),
    channel TEXT NOT NULL,
    amount  INTEGER NOT NULL,
    PRIMARY KEY (run_id, channel)
);

CREATE TABLE IF NOT EXISTS conversion_results (
    run_id          INTEGER NOT NULL REFERENCES runs(id),
    conversion_id   INTEGER NOT NULL,
    paid_search     INTEGER NOT NULL,
    organic_search  INTEGER NOT NULL,
    email           INTEGER NOT NULL,
    social          INTEGER NOT NULL,
    direct          INTEGER NOT NULL,
    referral        INTEGER NOT NULL,
    display_ad      INTEGER NOT NULL,
    webinar         INTEGER NOT NULL,
    content         INTEGER NOT NULL,
    unattributed    INTEGER NOT NULL,
    PRIMARY KEY (run_id, conversion_id)
);

CREATE INDEX IF NOT EXISTS idx_touchpoints_customer_day
    ON touchpoints (customer, day, id);

CREATE INDEX IF NOT EXISTS idx_conversions_customer_day
    ON conversions (customer, day);
"""


def ensure_schema(conn) -> None:
    """Create tables and indexes if they do not yet exist."""
    conn.executescript(SCHEMA_SQL)
