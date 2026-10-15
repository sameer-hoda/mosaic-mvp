"""Brief scheduler: three daily briefs at 09:00, 13:30, 18:30 Asia/Kolkata.

Pure helpers over an injected clock so tests never sleep. Quiet hours
(see briefs.py) suppress firing; the caller checks `briefs.should_ping`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Kolkata")
SLOTS = [(9, 0), (13, 30), (18, 30)]


def now_ist(ts: float | None = None) -> datetime:
    import time
    return datetime.fromtimestamp(time.time() if ts is None else ts, TZ)


def slot_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def due_slots(last_fired_key: str, now: datetime) -> list[tuple[int, int]]:
    """Slots today at/before `now` that fired after `last_fired_key`.

    `last_fired_key` is the slot_key of the last fired brief ("" if none).
    Returns slot (hour, min) tuples in time order.
    """
    due = []
    for h, m in SLOTS:
        slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if slot_dt <= now and slot_key(slot_dt) > last_fired_key:
            due.append((h, m))
    return due


def next_slot(now: datetime) -> datetime:
    for h, m in SLOTS:
        cand = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if cand > now:
            return cand
    return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
