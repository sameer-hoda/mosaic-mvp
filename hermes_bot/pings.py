#!/usr/bin/env python3
"""Real-time pings: one line + `draft N`, only when fully warranted.

A ping fires only when ALL hold: the message directly addresses the
owner, the owner has been silent in that chat for 20 minutes, and the
chat is unmuted. Max 6 pings/day, none in quiet hours (22:00–08:00
owner-local).
"""

import datetime
import logging

log = logging.getLogger("hermes_bot.pings")

OWNER_SILENCE_SECONDS = 20 * 60
PING_CAP_PER_DAY = 6
QUIET_START_HOUR = 22
QUIET_END_HOUR = 8


def is_direct_address(content: str, chat_jid: str, owner_tokens=()) -> bool:
    """True when the message is addressed to the owner.

    1:1 chats are direct by construction. In groups, the owner's name
    (or other token, e.g. phone) must be mentioned.
    """
    if not chat_jid.endswith("@g.us"):
        return True
    lowered = (content or "").lower()
    return any(t and t.lower() in lowered for t in (owner_tokens or ()))


def _in_quiet_hours(now: datetime.datetime) -> bool:
    hour = now.hour
    if QUIET_START_HOUR <= QUIET_END_HOUR:
        return QUIET_START_HOUR <= hour < QUIET_END_HOUR
    return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR


def should_ping(*, direct: bool, owner_silence_s: float, muted: bool,
                pings_today: int, now: datetime.datetime) -> tuple:
    """Gate a real-time ping. Returns (fire, reason)."""
    if not direct:
        return False, "not-direct"
    if owner_silence_s < OWNER_SILENCE_SECONDS:
        return False, "owner-active"
    if muted:
        return False, "muted"
    if pings_today >= PING_CAP_PER_DAY:
        return False, "cap"
    try:
        if _in_quiet_hours(now):
            return False, "quiet-hours"
    except Exception:
        pass
    return True, "ok"


def format_ping(chat_name: str, line: str, ref: str) -> str:
    """One line naming the chat + `draft N` pointer. Always two lines."""
    first = (line or "").strip().splitlines()[0][:160] if (line or "").strip() else "new message"
    return f"🔔 {chat_name}: {first}\ndraft {ref}"
