"""数据模型与常量定义。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

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

UNATTRIBUTED: str = "unattributed"

CHANNEL_INDEX: dict[str, int] = {name: i for i, name in enumerate(CHANNELS)}

LOOKBACK_DAYS: int = 89


@dataclass(frozen=True)
class Touchpoint:
    id: int
    customer: str
    channel: str
    date: date


@dataclass(frozen=True)
class Conversion:
    id: int
    customer: str
    date: date
    amount: int


@dataclass(frozen=True)
class ConversionResult:
    conversion_id: int
    amounts: dict[str, int]


@dataclass(frozen=True)
class RunSummary:
    run_id: int
    start_date: date
    end_date: date
    channel_totals: dict[str, int]
    unattributed: int
