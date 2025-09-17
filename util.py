import os
import pytz
from datetime import datetime, timedelta

# Таймзона проєкту
TZ = pytz.timezone(os.getenv("TZ", "Europe/Kyiv"))

def now_tz() -> datetime:
    """Поточний локальний час у налаштованій TZ."""
    return datetime.now(TZ)

def today_bounds_epoch():
    """
    Повертає межі "сьогодні" у вигляді (start_epoch_utc, end_epoch_utc),
    де end – НЕ включно. Враховує локальну таймзону Europe/Kyiv.
    """
    n = now_tz()
    start_local = TZ.localize(datetime(n.year, n.month, n.day, 0, 0, 0))
    end_local = start_local + timedelta(days=1)  # [start, end)
    start_utc = start_local.astimezone(pytz.UTC)
    end_utc = end_local.astimezone(pytz.UTC)
    return int(start_utc.timestamp()), int(end_utc.timestamp())

def next_run_at(hour=20, minute=0, second=0) -> datetime:
    """Наступний запуск у локальній TZ."""
    now = now_tz()
    target = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return target
