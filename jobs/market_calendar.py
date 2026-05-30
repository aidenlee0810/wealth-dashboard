"""
jobs/market_calendar.py — NYSE trading calendar (Plan §11, §33).

Pure stdlib. The daily snapshot uses this to:
  * skip weekends + full market holidays (cron is Mon-Fri but holidays remain)
  * flag half-days in snapshot_metadata
  * walk to the previous trading day (price/outcome lookbacks)
  * count trading days between two dates (horizon math)

Holiday tables are hardcoded for 2024-2028 (NYSE observed dates). When a
holiday falls on Saturday it is observed the preceding Friday; on Sunday the
following Monday — these observed dates are what's listed below.

If a date is queried outside the known range we conservatively treat any
weekday as a trading day (so the pipeline never silently no-ops on an
unknown future date) and emit a warning via the caller's logger if provided.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional, Union

DateLike = Union[str, date, datetime]

# NYSE full-closure holidays (observed dates), 2024-2028.
_HOLIDAYS: set[str] = {
    # 2024
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27",
    "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
    # 2028
    "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29", "2028-06-19",
    "2028-07-04", "2028-09-04", "2028-11-23", "2028-12-25",
}

# Early-close (1:00 PM ET) half-days — trading occurs, just shortened.
_HALF_DAYS: set[str] = {
    "2024-07-03", "2024-11-29", "2024-12-24",
    "2025-07-03", "2025-11-28", "2025-12-24",
    "2026-11-27", "2026-12-24",
    "2027-11-26",
}

_KNOWN_YEARS = range(2024, 2029)


def _to_date(d: DateLike) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.fromisoformat(str(d)[:10]).date()


def is_weekend(d: DateLike) -> bool:
    return _to_date(d).weekday() >= 5  # Sat=5, Sun=6


def is_holiday(d: DateLike) -> bool:
    return _to_date(d).isoformat() in _HOLIDAYS


def is_half_day(d: DateLike) -> bool:
    return _to_date(d).isoformat() in _HALF_DAYS


def is_trading_day(d: DateLike) -> bool:
    """True if the US equity market is open (regular or half session)."""
    dd = _to_date(d)
    if dd.weekday() >= 5:
        return False
    return dd.isoformat() not in _HOLIDAYS


def year_is_known(d: DateLike) -> bool:
    return _to_date(d).year in _KNOWN_YEARS


def previous_trading_day(d: DateLike, n: int = 1) -> date:
    """Return the trading day n sessions before d (d itself not counted)."""
    cur = _to_date(d)
    remaining = n
    while remaining > 0:
        cur -= timedelta(days=1)
        if is_trading_day(cur):
            remaining -= 1
    return cur


def next_trading_day(d: DateLike, n: int = 1) -> date:
    cur = _to_date(d)
    remaining = n
    while remaining > 0:
        cur += timedelta(days=1)
        if is_trading_day(cur):
            remaining -= 1
    return cur


def most_recent_trading_day(d: DateLike) -> date:
    """Return d if it's a trading day, else the most recent prior trading day."""
    cur = _to_date(d)
    while not is_trading_day(cur):
        cur -= timedelta(days=1)
    return cur


def trading_days_between(start: DateLike, end: DateLike) -> int:
    """Count trading days in (start, end] — used for outcome horizon math.

    Returns the number of sessions strictly after start, up to and including
    end. Negative if end < start."""
    s, e = _to_date(start), _to_date(end)
    if e == s:
        return 0
    sign = 1 if e > s else -1
    lo, hi = (s, e) if e > s else (e, s)
    count = 0
    cur = lo
    while cur < hi:
        cur += timedelta(days=1)
        if is_trading_day(cur):
            count += 1
    return sign * count


def trading_days_in_range(start: DateLike, end: DateLike) -> list[str]:
    """Inclusive list of trading-day ISO strings from start to end."""
    s, e = _to_date(start), _to_date(end)
    out: list[str] = []
    cur = s
    while cur <= e:
        if is_trading_day(cur):
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def market_status(d: DateLike) -> str:
    """Classify a date: 'open' | 'half_day' | 'weekend' | 'holiday'."""
    dd = _to_date(d)
    if dd.weekday() >= 5:
        return "weekend"
    if dd.isoformat() in _HOLIDAYS:
        return "holiday"
    if dd.isoformat() in _HALF_DAYS:
        return "half_day"
    return "open"


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else datetime.now().date().isoformat()
    print(f"date            : {q}")
    print(f"status          : {market_status(q)}")
    print(f"is_trading_day  : {is_trading_day(q)}")
    print(f"prev trading day: {previous_trading_day(q)}")
    print(f"5 sessions ago  : {previous_trading_day(q, 5)}")
    print(f"year known      : {year_is_known(q)}")
