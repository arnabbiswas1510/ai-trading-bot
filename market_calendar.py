"""NYSE trading calendar: holidays, trading-day arithmetic, and RTH checks.

Extracted verbatim from execution_agent.py on 2026-09-18. Pure functions with no
brokerage, database or network dependency -- every day-gated exit rule in
exit_rules.py counts holding days through trading_days_between(), so an error
here silently shifts every one of them.

See decisions/2026-09-18_execution-agent-split.md.
"""

import datetime
from zoneinfo import ZoneInfo

def _is_rth_now() -> bool:
    """True if US regular trading hours (Mon–Fri, 09:30–16:00 ET) right now.

    Cheap, dependency-free helper used only to colour the IBKR-disconnect alert:
    a broker outage during RTH means exits are actively not firing, which is more
    urgent than the same outage overnight. Does not account for market holidays —
    a false positive on a holiday only makes the alert slightly louder, never
    quieter, so it fails safe.
    """
    now = datetime.datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return False
    return (now.hour == 9 and now.minute >= 30) or (10 <= now.hour < 16)

# ── NYSE trading-day calendar ─────────────────────────────────────────────────
def _nyse_holidays(year: int) -> set:
    """Return the set of NYSE market holidays for a given year.

    Computed algorithmically — no external package required.
    Includes the observed (Mon/Fri substitute) date when a holiday falls on a
    weekend, matching the NYSE official schedule.
    """
    from calendar import monthcalendar, MONDAY, THURSDAY

    def _observed(d: datetime.date) -> datetime.date:
        """Shift Sat → Fri, Sun → Mon for observed holiday."""
        if d.weekday() == 5:  # Saturday
            return d - datetime.timedelta(days=1)
        if d.weekday() == 6:  # Sunday
            return d + datetime.timedelta(days=1)
        return d

    def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime.date:
        """Return the nth occurrence of weekday (0=Mon..6=Sun) in month/year."""
        weeks = monthcalendar(year, month)
        hits = [w[weekday] for w in weeks if w[weekday] != 0]
        return datetime.date(year, month, hits[n - 1])

    def _last_weekday(year: int, month: int, weekday: int) -> datetime.date:
        """Return the last occurrence of weekday in month/year."""
        weeks = monthcalendar(year, month)
        hits = [w[weekday] for w in weeks if w[weekday] != 0]
        return datetime.date(year, month, hits[-1])

    holidays = set()

    # New Year's Day — Jan 1 (observed)
    holidays.add(_observed(datetime.date(year, 1, 1)))
    # MLK Day — 3rd Monday in January
    holidays.add(_nth_weekday(year, 1, MONDAY, 3))
    # Presidents' Day — 3rd Monday in February
    holidays.add(_nth_weekday(year, 2, MONDAY, 3))
    # Good Friday — 2 days before Easter Sunday
    # Easter via Anonymous Gregorian algorithm
    a, b, c = year % 19, year // 100, year % 100
    d_, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d_ - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    easter_month = (h + l_ - 7 * m + 114) // 31
    easter_day   = ((h + l_ - 7 * m + 114) % 31) + 1
    easter = datetime.date(year, easter_month, easter_day)
    holidays.add(easter - datetime.timedelta(days=2))  # Good Friday
    # Memorial Day — last Monday in May
    holidays.add(_last_weekday(year, 5, MONDAY))
    # Juneteenth — Jun 19 (observed), added from 2022
    if year >= 2022:
        holidays.add(_observed(datetime.date(year, 6, 19)))
    # Independence Day — Jul 4 (observed)
    holidays.add(_observed(datetime.date(year, 7, 4)))
    # Labor Day — 1st Monday in September
    holidays.add(_nth_weekday(year, 9, MONDAY, 1))
    # Thanksgiving — 4th Thursday in November
    holidays.add(_nth_weekday(year, 11, THURSDAY, 4))
    # Christmas — Dec 25 (observed)
    holidays.add(_observed(datetime.date(year, 12, 25)))

    return holidays

def trading_days_between(start: datetime.date, end: datetime.date) -> int:
    """Count NYSE trading days in the half-open interval [start, end).

    Weekends and NYSE market holidays are excluded.  This is used for plateau
    detection so a 3-day weekend (e.g. Labor Day) doesn't artificially advance
    the stall counter.

    Args:
        start: The earlier date (inclusive).
        end:   The later date (exclusive — typically 'today').

    Returns:
        Number of trading days between start and end (>= 0).
    """
    if end <= start:
        return 0
    # Pre-compute holidays for all years in range
    years = range(start.year, end.year + 1)
    holidays: set = set()
    for y in years:
        holidays |= _nyse_holidays(y)

    count = 0
    current = start
    one_day = datetime.timedelta(days=1)
    while current < end:
        if current.weekday() < 5 and current not in holidays:  # Mon–Fri, not a holiday
            count += 1
        current += one_day
    return count
