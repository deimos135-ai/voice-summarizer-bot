import os
import pytz
from datetime import datetime, timedelta

# Таймзона проєкту (можеш змінити змінною середовища TZ)
TZ = pytz.timezone(os.getenv("TZ", "Europe/Kyiv"))

def now_tz() -> datetime:
    """Поточний локальний час у налаштованій TZ."""
    return datetime.now(TZ)

def today_bounds_epoch():
    """
    Межі 'сьогодні' у ЛОКАЛЬНІЙ TZ -> (start_utc_epoch, end_utc_epoch), де end НЕ включно.
    Важливо: використовуємо replace(...) на вже таймзонованому now_tz(),
    щоб коректно проходити через DST/зсуви.
    """
    n = now_tz()
    start_local = n.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)  # [start, end)

    start_utc = start_local.astimezone(pytz.UTC)
    end_utc = end_local.astimezone(pytz.UTC)
    return int(start_utc.timestamp()), int(end_utc.timestamp())

def next_run_at(hour=20, minute=0, second=0) -> datetime:
    """Повертає локальний datetime наступного запуску (за замовчуванням 20:00)."""
    now = now_tz()
    target = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return target
