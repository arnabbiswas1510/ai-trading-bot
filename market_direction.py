"""Pure moving-average market gate; data acquisition belongs to callers."""
import datetime as dt
import math


def index_verdict(rows, *, today, window, slope_days, buffer_pct, max_stale_days):
    if window <= 0 or slope_days <= 0 or len(rows) < window + slope_days:
        return None
    dates = [dt.date.fromisoformat(str(row[0])[:10]) for row in rows]
    closes = [float(row[1]) for row in rows]
    if dates != sorted(set(dates)) or any(not math.isfinite(c) or c <= 0 for c in closes):
        return None
    if dates[-1] > today or (today - dates[-1]).days > max_stale_days:
        return None
    now = sum(closes[-window:]) / window
    then = sum(closes[-window - slope_days:-slope_days]) / window
    return closes[-1] > now * (1 + buffer_pct), then > 0 and (now - then) / then >= 0
