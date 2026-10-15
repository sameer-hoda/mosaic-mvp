#!/usr/bin/env python3
"""Worker G: one full pipeline pass — scan → extract → brief → draft.

Dry-run by default: the snapshot is opened read-only, state lives in
MOSAIC_DATA_DIR, and this module only prints/logs — zero sends, and zero
network unless the caller injects an `llm_fn`. `main.py` runs one pass on
a timer thread (env-gated); `scripts/e2e_dry_run.py` runs one pass and
prints the report.
"""

from __future__ import annotations

import datetime
import os
import sqlite3

DRAFT_THREAD_LINES = 20


def cycle_enabled() -> bool:
    """True only when explicitly enabled (default off).

    The scanner reads the test snapshot; flip MOSAIC_CYCLE=1 once the
    scanner supports the live store. The e2e script exercises the same
    run_cycle() path either way.
    """
    return os.getenv("MOSAIC_CYCLE", "0") == "1"


def cycle_interval_s() -> float:
    try:
        return max(60.0, float(os.getenv("MOSAIC_CYCLE_INTERVAL_S", "900") or 900))
    except ValueError:
        return 900.0


def eval_pings(store_conn: sqlite3.Connection, snap_conn: sqlite3.Connection,
               owner_tokens: tuple = (), limit: int = 20,
               now: datetime.datetime | None = None,
               pings_today: int = 0) -> dict:
    """Evaluate ping rules over the latest message of recent chats.

    Count-only: never sends, never writes to the snapshot.
    """
    from hermes_bot import pings as pg
    from hermes_bot import store as st

    now = now or datetime.datetime.now().astimezone()
    try:
        chats = snap_conn.execute(
            "SELECT jid FROM chats WHERE jid LIKE '%@g.us' "
            "ORDER BY last_message_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except Exception:
        return {"fired": 0, "suppressed": {"error": 1}}
    fired = 0
    suppressed: dict[str, int] = {}
    for (jid,) in chats:
        last = snap_conn.execute(
            "SELECT content, timestamp, is_from_me FROM messages "
            "WHERE chat_jid = ? AND content != '' "
            "ORDER BY timestamp DESC LIMIT 1",
            (jid,),
        ).fetchone()
        if not last:
            continue
        own = snap_conn.execute(
            "SELECT MAX(timestamp) m FROM messages WHERE chat_jid = ? "
            "AND is_from_me = 1",
            (jid,),
        ).fetchone()
        try:
            if own and own["m"]:
                own_t = datetime.datetime.fromisoformat(
                    str(own["m"]).replace(" ", "T"))
                if own_t.tzinfo is None:
                    own_t = own_t.replace(tzinfo=datetime.timezone.utc)
                silence = (now - own_t).total_seconds()
            else:
                silence = float("inf")
        except ValueError:
            continue
        direct = pg.is_direct_address(last["content"] or "", jid, owner_tokens)
        fire, reason = pg.should_ping(
            direct=direct, owner_silence_s=silence,
            muted=st.is_muted(store_conn, jid),
            pings_today=pings_today, now=now)
        if fire:
            fired += 1
        else:
            suppressed[reason] = suppressed.get(reason, 0) + 1
    return {"fired": fired, "suppressed": suppressed}


def draft_sample(store_conn: sqlite3.Connection, snap_conn: sqlite3.Connection,
                 owner_names: list[str] | None = None) -> dict:
    """Draft one heuristic reply for the top-ranked open item.

    Heuristic only (no LLM): deterministic and network-free.
    Returns {} when there is nothing actionable.
    """
    from hermes_bot import briefs
    from hermes_bot import draft as dr

    owner_names = owner_names or []
    ranked = briefs.owner_next_items(
        briefs.fetch_open_items(store_conn), owner_names)
    if not ranked:
        return {}
    item = ranked[0]
    chat_jid = item.get("chat_jid", "")
    try:
        rows = snap_conn.execute(
            "SELECT sender, content FROM messages WHERE chat_jid = ? "
            "AND content != '' ORDER BY timestamp DESC LIMIT ?",
            (chat_jid, DRAFT_THREAD_LINES),
        ).fetchall()
        thread = [f"{r['sender']}: {r['content']}" for r in reversed(rows)]
    except Exception:
        thread = []
    try:
        name = snap_conn.execute(
            "SELECT name FROM chats WHERE jid = ?", (chat_jid,)).fetchone()
        chat_name = (name["name"] if name and name["name"] else chat_jid)
    except Exception:
        chat_name = chat_jid
    options = dr.build_drafts(item, thread)["options"]
    text = dr.format_draft_reply("1", chat_name, item.get("what", ""), options)
    return {"chat_jid": chat_jid, "chat_name": chat_name,
            "what": item.get("what", ""), "text": text}


def run_cycle(store_conn: sqlite3.Connection,
              owner_names: list[str] | None = None,
              llm_fn=None, limit_chats: int = 20,
              dry_run: bool = True, deliver=None,
              now: float | None = None,
              slot: str = "Morning brief") -> dict:
    """One full pass: scan → extract → brief → ping-eval → draft.

    scan always runs at scan-level dry-run (no send_fn is ever passed),
    so items/cursors update but nothing is emitted. The brief text is
    handed to `deliver` ONLY when dry_run=False and deliver is set;
    otherwise it is returned print-only in the report.
    """
    import time as _time

    from hermes_bot import briefs
    from hermes_bot import scanner as sc

    owner_names = owner_names or []
    now = _time.time() if now is None else now

    summary = sc.scan(store_conn, limit_chats=limit_chats,
                      llm_fn=llm_fn, dry_run=True)
    text, brief_no = briefs.build_brief(store_conn, owner_names,
                                       slot=slot, now=now)
    delivered = False
    if not dry_run and deliver is not None:
        deliver(text)
        delivered = True

    snap = sc.open_snapshot()
    try:
        ping_stat = eval_pings(store_conn, snap, tuple(owner_names))
        sample = draft_sample(store_conn, snap, owner_names)
    finally:
        snap.close()

    return {
        "chats": summary["chats"],
        "messages": summary["messages"],
        "items": summary["items"],
        "llm_calls": summary["llm_calls"],
        "per_chat": summary["per_chat"],
        "open_items": len(briefs.fetch_open_items(store_conn)),
        "brief_no": brief_no,
        "brief_text": text,
        "delivered": delivered,
        "pings_fired": ping_stat["fired"],
        "pings_suppressed": ping_stat["suppressed"],
        "draft": sample,
        "dry_run": dry_run,
    }
