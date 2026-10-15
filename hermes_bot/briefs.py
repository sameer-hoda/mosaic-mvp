"""Daily briefs: only items where the next move is the owner's.

Three slots a day (09:00, 13:30, 18:30 Asia/Kolkata — see scheduler.py).
Ranking: direct-question > promised > owns > waiting-on.
Each brief shows max 5 numbered items plus max 2 "also moved" lines.
An empty brief is one line, never silence.

Numbered actions (`done N`, `later N`, `more N`) resolve through the
store.item_alias table (brief_no map, 24 h expiry — see store.py).
`quiet 22-8` sets quiet hours (default 22:00-08:00): no pings then.
`pulse` renders a fresh brief on demand.

This module never sends anything and never touches the network; callers
decide whether to enqueue the rendered text.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

from hermes_bot import store as st

MAX_ITEMS = 5
MAX_ALSO_MOVED = 2

DEFAULT_QUIET_START = 22
DEFAULT_QUIET_END = 8

SLOT_NAMES = {(9, 0): "Morning brief", (13, 30): "Midday brief", (18, 30): "Evening brief"}


def _is_owner(item: dict, owner_names: list[str]) -> bool:
    own = {(n or "").strip().lower() for n in owner_names if n}
    return (item.get("owner") or "").strip().lower() in own


def _is_requester(item: dict, owner_names: list[str]) -> bool:
    own = {(n or "").strip().lower() for n in owner_names if n}
    return (item.get("requester") or "").strip().lower() in own


def _has_question(item: dict) -> bool:
    return "?" in f"{item.get('what', '')} {item.get('context', '')}"


def rank_of(item: dict, owner_names: list[str] | None = None) -> int:
    """0 direct-question, 1 promised, 2 owns, 3 waiting-on, 99 not rankable."""
    owner_names = owner_names or []
    if (item.get("status") or "open") != "open":
        return 99
    mine = _is_owner(item, owner_names)
    if mine and item.get("kind") == "ask" and _has_question(item):
        return 0
    if mine and item.get("kind") == "commitment":
        return 1
    if mine:
        return 2
    if _is_requester(item, owner_names):
        return 3
    if not (item.get("owner") or "").strip():
        return 2
    return 99


def owner_next_items(items: list[dict], owner_names: list[str] | None = None) -> list[dict]:
    """Only items where the next move is the owner's, best rank first."""
    ranked = [(rank_of(i, owner_names), i) for i in items]
    kept = [(r, i) for r, i in ranked if r < 99]
    kept.sort(key=lambda t: (t[0], str(t[1].get("what", ""))))
    return [i for _, i in kept]


def fetch_open_items(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM items WHERE status = 'open'").fetchall()
    return [dict(r) for r in rows]


def _next_brief_no(conn: sqlite3.Connection) -> int:
    try:
        n = int(st.config_get(conn, "brief_no", "0"))
    except ValueError:
        n = 0
    n += 1
    st.config_set(conn, "brief_no", str(n))
    return n


def _fmt_item(n: int, item: dict) -> str:
    chat = item.get("chat_jid", "")
    extra = ""
    if item.get("due"):
        extra += f" (due {item['due']})"
    return f"{n}. [{chat}] {item.get('what', '')}{extra}"


def render_brief(items: list[dict], slot: str = "Morning brief",
                 also_moved: list[dict] | None = None,
                 when: datetime | None = None) -> tuple[str, list[dict], list[dict]]:
    """Render brief text. Returns (text, shown, also_shown). Never empty."""
    shown = items[:MAX_ITEMS]
    also_shown = list(also_moved or [])[:MAX_ALSO_MOVED]
    day = (when or datetime.now(timezone.utc)).strftime("%a %d %b")
    if not shown and not also_shown:
        return f"{slot} ({day}) — all clear. Nothing needs you right now.", [], []
    lines = [f"{slot} ({day}) — {len(shown)} need you:"]
    lines += [_fmt_item(n + 1, it) for n, it in enumerate(shown)]
    for it in also_shown:
        lines.append(f"Also moved: [{it.get('chat_jid', '')}] {it.get('what', '')}")
    return "\n".join(lines), shown, also_shown


def build_brief(conn: sqlite3.Connection, owner_names: list[str] | None = None,
                slot: str = "Morning brief",
                also_moved: list[dict] | None = None,
                now: float | None = None) -> tuple[str, int]:
    """Rank open items, render, and record the brief_no alias map."""
    now = time.time() if now is None else now
    items = owner_next_items(fetch_open_items(conn), owner_names)
    text, shown, _ = render_brief(items, slot, also_moved)
    brief_no = _next_brief_no(conn)
    if shown:
        st.set_alias_map(conn, brief_no, {str(n + 1): it["id"] for n, it in enumerate(shown)},
                         now=now)
    return text, brief_no


def pulse(conn: sqlite3.Connection, owner_names: list[str] | None = None,
          now: float | None = None) -> tuple[str, int]:
    """Fresh brief on demand (same shape as a scheduled one)."""
    return build_brief(conn, owner_names, slot="Pulse", now=now)


# ── quiet hours ──────────────────────────────────────────────────────────

def set_quiet_hours(conn: sqlite3.Connection, start: int, end: int) -> str:
    st.config_set(conn, "quiet_start", str(start % 24))
    st.config_set(conn, "quiet_end", str(end % 24))
    return f"Quiet hours set: {start % 24}:00–{end % 24}:00. No pings then."


def get_quiet_hours(conn: sqlite3.Connection) -> tuple[int, int]:
    try:
        s = int(st.config_get(conn, "quiet_start", str(DEFAULT_QUIET_START)))
    except ValueError:
        s = DEFAULT_QUIET_START
    try:
        e = int(st.config_get(conn, "quiet_end", str(DEFAULT_QUIET_END)))
    except ValueError:
        e = DEFAULT_QUIET_END
    return s % 24, e % 24


def in_quiet_hours(conn: sqlite3.Connection, hour: int) -> bool:
    """True when `hour` (0-23 local) falls inside quiet hours."""
    s, e = get_quiet_hours(conn)
    hour = hour % 24
    if s == e:
        return False
    if s < e:
        return s <= hour < e
    return hour >= s or hour < e


def should_ping(conn: sqlite3.Connection, hour: int) -> bool:
    return not in_quiet_hours(conn, hour)


# ── numbered actions ─────────────────────────────────────────────────────

def do_done(conn: sqlite3.Connection, brief_no: int, alias: str,
            now: float | None = None) -> str:
    item = st.resolve_alias(conn, brief_no, str(alias), now=now)
    if item is None:
        return f"No item {alias} on brief {brief_no} (unknown or expired after 24 h)."
    conn.execute("UPDATE items SET status='done', updated_at=? WHERE id=?",
                 (time.time() if now is None else now, item["id"]))
    conn.commit()
    return f"Done: {item.get('what', '')}"


def do_later(conn: sqlite3.Connection, brief_no: int, alias: str,
             now: float | None = None) -> str:
    item = st.resolve_alias(conn, brief_no, str(alias), now=now)
    if item is None:
        return f"No item {alias} on brief {brief_no} (unknown or expired after 24 h)."
    conn.execute("UPDATE items SET due='tomorrow', updated_at=? WHERE id=?",
                 (time.time() if now is None else now, item["id"]))
    conn.commit()
    return f"Snoozed to tomorrow: {item.get('what', '')}"


def do_more(conn: sqlite3.Connection, brief_no: int, alias: str,
            now: float | None = None) -> str:
    item = st.resolve_alias(conn, brief_no, str(alias), now=now)
    if item is None:
        return f"No item {alias} on brief {brief_no} (unknown or expired after 24 h)."
    bits = [f"What: {item.get('what', '')}"]
    if item.get("context"):
        bits.append(f"Context: {item['context']}")
    bits.append(f"Chat: {item.get('chat_jid', '')}")
    if item.get("owner"):
        bits.append(f"Owner: {item['owner']}")
    if item.get("due"):
        bits.append(f"Due: {item['due']}")
    return "\n".join(bits)


def parse_command(conn: sqlite3.Connection, text: str, brief_no: int,
                  owner_names: list[str] | None = None,
                  now: float | None = None) -> str | None:
    """Handle self-chat commands. Returns reply text, or None if not a command.

    Commands: `done N`, `later N`, `more N`, `quiet 22-8`, `pulse`.
    `brief_no` is the latest brief the sender saw.
    """
    parts = (text or "").strip().lower().split()
    if not parts:
        return None
    verb = parts[0]
    if verb in ("done", "later", "more") and len(parts) == 2:
        fn = {"done": do_done, "later": do_later, "more": do_more}[verb]
        return fn(conn, brief_no, parts[1], now=now)
    if verb == "quiet" and len(parts) == 2:
        import re
        m = re.match(r"(\d{1,2})\s*-\s*(\d{1,2})$", parts[1])
        if m:
            return set_quiet_hours(conn, int(m.group(1)), int(m.group(2)))
        return "Usage: quiet 22-8"
    if verb == "pulse" and len(parts) == 1:
        out, _ = pulse(conn, owner_names, now=now)
        return out
    return None
