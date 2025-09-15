import os
import pytz
from datetime import datetime

TZ = pytz.timezone(os.getenv("TZ", "Europe/Kyiv"))

def now_tz():
    return datetime.now(TZ)

def today_bounds():
    n = now_tz()
    start = TZ.localize(datetime(n.year, n.month, n.day, 0, 0, 0))
    end = TZ.localize(datetime(n.year, n.month, n.day, 23, 59, 59))
    return start, end
