"""Helpers for completed and upcoming mainland A-share trading sessions."""

from __future__ import annotations

from datetime import time
import logging
from typing import Iterable

import pandas as pd


LOGGER = logging.getLogger(__name__)
MARKET_DATA_READY_TIME = time(15, 30)


def _normalise_dates(dates: Iterable[object]) -> pd.DatetimeIndex:
    result = pd.DatetimeIndex(pd.to_datetime(list(dates), errors="coerce")).dropna().normalize().unique().sort_values()
    return result


def load_a_share_calendar() -> pd.DatetimeIndex | None:
    """Load the Sina/AKShare A-share calendar, falling back only when unavailable."""
    try:
        import akshare as ak  # type: ignore

        frame = ak.tool_trade_date_hist_sina()
        column = next((name for name in ("trade_date", "交易日期", "date") if name in frame.columns), None)
        if column is None:
            raise ValueError("trade calendar response has no date column")
        dates = _normalise_dates(frame[column])
        if dates.empty:
            raise ValueError("trade calendar response is empty")
        return dates
    except Exception as exc:  # pragma: no cover - network/API instability
        LOGGER.warning("A-share trading calendar unavailable; falling back to weekdays: %s", exc)
        return None


def _as_shanghai_timestamp(value: pd.Timestamp | str | None) -> pd.Timestamp:
    if value is None:
        return pd.Timestamp.now(tz="Asia/Shanghai")
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("Asia/Shanghai")
    return timestamp.tz_convert("Asia/Shanghai")


def _weekday_on_or_before(date: pd.Timestamp) -> pd.Timestamp:
    result = date.normalize()
    while result.weekday() >= 5:
        result -= pd.Timedelta(days=1)
    return result


def latest_completed_a_share_trading_day(
    as_of: pd.Timestamp | str | None = None,
    calendar: Iterable[object] | None = None,
) -> pd.Timestamp:
    """Return the last session whose daily bar should be complete at ``as_of``.

    Before 15:30 China Standard Time, today's session is deliberately excluded.
    The real trading calendar handles weekends and exchange holidays; weekday
    fallback keeps the update command usable if that endpoint is unavailable.
    """
    timestamp = _as_shanghai_timestamp(as_of)
    # Calendar entries are date-only (timezone-naive); compare them with a
    # date-only local cutoff after using the Shanghai clock for the close time.
    cutoff = timestamp.tz_localize(None).normalize()
    if timestamp.time() < MARKET_DATA_READY_TIME:
        cutoff -= pd.Timedelta(days=1)

    dates = _normalise_dates(calendar) if calendar is not None else load_a_share_calendar()
    if dates is not None and not dates.empty:
        eligible = dates[dates <= cutoff]
        if not eligible.empty:
            return pd.Timestamp(eligible[-1])
    return _weekday_on_or_before(cutoff)


def next_a_share_trading_day(
    date: pd.Timestamp | str,
    calendar: Iterable[object] | None = None,
) -> pd.Timestamp:
    """Return the first A-share session strictly after ``date``."""
    current = pd.Timestamp(date)
    if current.tzinfo is not None:
        current = current.tz_convert("Asia/Shanghai").tz_localize(None)
    current = current.normalize()
    dates = _normalise_dates(calendar) if calendar is not None else load_a_share_calendar()
    if dates is not None and not dates.empty:
        future = dates[dates > current]
        if not future.empty:
            return pd.Timestamp(future[0])
    return (current + pd.offsets.BDay(1)).normalize()
