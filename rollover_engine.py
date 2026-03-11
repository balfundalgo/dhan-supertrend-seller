"""
rollover_engine.py
==================
Handles all time-based rollover and expiry selection logic for the
Supertrend Option Selling Strategy.

Rollover Variant 1: Roll on 3rd weekly expiry of the month @ 3:00 PM IST
Rollover Variant 2: Roll 4 trading days before monthly expiry @ 3:00 PM IST

Also provides:
  - NSE holiday calendar (2025-2026)
  - Trading day counting for expiry selection (1-15 = current month, >15 = next)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# NSE Trading Holiday Calendar
# Update this set each year with NSE's official holiday list.
# Dates that fall on weekends are NOT included (market already closed).
# ---------------------------------------------------------------------------
NSE_HOLIDAYS: Set[date] = {
    # ── 2025 ────────────────────────────────────────────────────────────────
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr (Ramzan Eid)
    date(2025, 4, 10),   # Shri Ram Navami
    date(2025, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5,  1),   # Maharashtra Day
    date(2025, 8, 15),   # Independence Day
    date(2025, 10, 2),   # Gandhi Jayanti
    date(2025, 10, 20),  # Diwali Laxmi Puja
    date(2025, 10, 21),  # Diwali Balipratipada
    date(2025, 11,  5),  # Guru Nanak Jayanti / Prakash Gurpurb
    date(2025, 12, 25),  # Christmas

    # ── 2026 ────────────────────────────────────────────────────────────────
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 20),   # Holi
    date(2026, 4,  3),   # Good Friday
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    date(2026, 5,  1),   # Maharashtra Day
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 12, 25),  # Christmas
}

IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Trading day helpers
# ---------------------------------------------------------------------------

def is_trading_day(d: date, holidays: Set[date] = NSE_HOLIDAYS) -> bool:
    """True if `d` is a weekday and not in the NSE holiday set."""
    return d.weekday() < 5 and d not in holidays


def count_trading_days_this_month(today: date, holidays: Set[date] = NSE_HOLIDAYS) -> int:
    """
    Count trading days from the 1st of the current month up to and
    including `today`.
    """
    first = today.replace(day=1)
    count = 0
    d = first
    while d <= today:
        if is_trading_day(d, holidays):
            count += 1
        d += timedelta(days=1)
    return count


def count_trading_days_between(
    start: date, end: date, holidays: Set[date] = NSE_HOLIDAYS
) -> int:
    """Count trading days in [start, end] inclusive."""
    count = 0
    d = start
    while d <= end:
        if is_trading_day(d, holidays):
            count += 1
        d += timedelta(days=1)
    return count


def get_nth_trading_day_before(
    target: date, n: int, holidays: Set[date] = NSE_HOLIDAYS
) -> date:
    """
    Return the date that is exactly `n` trading days before `target`
    (not counting `target` itself).
    """
    d = target - timedelta(days=1)
    found = 0
    while True:
        if is_trading_day(d, holidays):
            found += 1
            if found >= n:
                return d
        d -= timedelta(days=1)
        if d < target - timedelta(days=365):  # safety guard
            return target - timedelta(days=n * 2)


# ---------------------------------------------------------------------------
# Expiry classification helpers
# ---------------------------------------------------------------------------

def parse_expiry_dates(expiry_list: List[str]) -> List[Tuple[date, str]]:
    """Parse raw expiry strings into sorted (date, str) tuples."""
    parsed: List[Tuple[date, str]] = []
    for x in expiry_list:
        try:
            parsed.append((datetime.strptime(x.strip(), "%Y-%m-%d").date(), x.strip()))
        except Exception:
            continue
    parsed.sort(key=lambda t: t[0])
    return parsed


def group_expiries_by_month(
    parsed: List[Tuple[date, str]]
) -> Dict[Tuple[int, int], List[Tuple[date, str]]]:
    """Group parsed expiries by (year, month)."""
    groups: Dict[Tuple[int, int], List[Tuple[date, str]]] = {}
    for d, s in parsed:
        key = (d.year, d.month)
        groups.setdefault(key, []).append((d, s))
    return groups


def get_monthly_expiry_for_month(
    year: int, month: int, groups: Dict[Tuple[int, int], List[Tuple[date, str]]]
) -> Optional[Tuple[date, str]]:
    """Monthly expiry = last expiry of the month in the list."""
    month_expiries = groups.get((year, month), [])
    if not month_expiries:
        return None
    return month_expiries[-1]  # already sorted ascending


def get_3rd_weekly_expiry_for_month(
    year: int, month: int, groups: Dict[Tuple[int, int], List[Tuple[date, str]]]
) -> Optional[Tuple[date, str]]:
    """
    3rd weekly expiry = 3rd expiry in the month when sorted ascending.
    Returns None if month has fewer than 3 expiries.
    """
    month_expiries = groups.get((year, month), [])
    if len(month_expiries) < 3:
        return None
    return month_expiries[2]  # 0-indexed, so index 2 = 3rd expiry


# ---------------------------------------------------------------------------
# Rollover trigger checks
# ---------------------------------------------------------------------------

def get_rollover_v1_date(expiry_list: List[str]) -> Optional[date]:
    """Return the 3rd weekly expiry date of the current month."""
    today = date.today()
    parsed = parse_expiry_dates(expiry_list)
    groups = group_expiries_by_month(parsed)
    result = get_3rd_weekly_expiry_for_month(today.year, today.month, groups)
    return result[0] if result else None


def get_rollover_v2_date(
    expiry_list: List[str], holidays: Set[date] = NSE_HOLIDAYS
) -> Optional[date]:
    """Return 4 trading days before the current month's monthly expiry."""
    today = date.today()
    parsed = parse_expiry_dates(expiry_list)
    groups = group_expiries_by_month(parsed)
    monthly = get_monthly_expiry_for_month(today.year, today.month, groups)
    if not monthly:
        return None
    return get_nth_trading_day_before(monthly[0], 4, holidays)


def should_rollover_v1_now(expiry_list: List[str]) -> bool:
    """
    True when:
      - today is the 3rd weekly expiry of the current month, AND
      - current IST time >= 15:00
    """
    now = datetime.now(tz=IST)
    if now.hour < 15:
        return False
    rollover_date = get_rollover_v1_date(expiry_list)
    return rollover_date is not None and rollover_date == now.date()


def should_rollover_v2_now(
    expiry_list: List[str], holidays: Set[date] = NSE_HOLIDAYS
) -> bool:
    """
    True when:
      - today is 4 trading days before the current month's monthly expiry, AND
      - current IST time >= 15:00
    """
    now = datetime.now(tz=IST)
    if now.hour < 15:
        return False
    rollover_date = get_rollover_v2_date(expiry_list, holidays)
    return rollover_date is not None and rollover_date == now.date()


def get_next_monthly_expiry_str(
    expiry_list: List[str], current_expiry_text: str
) -> Optional[str]:
    """
    Given the current position's expiry text, return the next month's
    monthly expiry string for use in the rollover re-entry.
    """
    try:
        current_date = datetime.strptime(current_expiry_text.strip(), "%Y-%m-%d").date()
    except Exception:
        return None

    parsed = parse_expiry_dates(expiry_list)
    groups = group_expiries_by_month(parsed)

    # Find the next month after current expiry's month
    year, month = current_date.year, current_date.month
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

    monthly = get_monthly_expiry_for_month(next_year, next_month, groups)
    return monthly[1] if monthly else None


def rollover_info(expiry_list: List[str], holidays: Set[date] = NSE_HOLIDAYS) -> str:
    """Return a human-readable summary of upcoming rollover dates."""
    v1 = get_rollover_v1_date(expiry_list)
    v2 = get_rollover_v2_date(expiry_list, holidays)
    return (
        f"Rollover dates | "
        f"V1(3rd weekly)={v1.isoformat() if v1 else '-'} | "
        f"V2(4td before expiry)={v2.isoformat() if v2 else '-'}"
    )
