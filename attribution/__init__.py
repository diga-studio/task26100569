"""Daily-granularity time-decay attribution library backed by SQLite."""

from .engine import run_attribution
from .schema import CHANNELS, WINDOW_DAYS
from .store import (
    connect,
    get_conversion_result,
    get_run_summary,
    import_conversions,
    import_touchpoints,
    iter_conversion_results,
)

__all__ = [
    "CHANNELS",
    "WINDOW_DAYS",
    "connect",
    "import_touchpoints",
    "import_conversions",
    "run_attribution",
    "get_run_summary",
    "get_conversion_result",
    "iter_conversion_results",
]
