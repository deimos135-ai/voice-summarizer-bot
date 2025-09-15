import os
import pytz
from datetime import datetime, time, timedelta

TZ = pytz.timezone(os.getenv("TZ", "Europe/Kyiv"))

def now_tz():
    return datetime.now(TZ)

def today_bounds():
    n = now_tz()
    start = TZ.localize(datetime(n.year, n.month, n.day, 0, 0, 0))
    end = TZ.localize(datetime(n.year, n.month, n.day, 23, 59, 59))
    return start, end

def next_run_at(hour=20, minute=0, second=0):
    now = now_tz()
    target = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return target
