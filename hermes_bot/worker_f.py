#!/usr/bin/env python3
"""Worker F hookup: draft/send commands + real-time pings.

Owns the main.py/brain.py wiring only. Briefs/ask/extract internals
are never touched: items are read through store.get_item/resolve_alias,
threads through db.get_chat_messages.
"""

import datetime
import logging
import re
import time

log = logging.getLogger("hermes_bot.worker_f")

_DRAFT_RE = re.compile(r"^draft\s+([A-Za-z0-9]+)\s*$", re.IGNORECASE)


def _today_start_ts(now: float) -> float:
    import datetime as _dt
    day = _dt.datetime.fromtimestamp(now).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return day.timestamp()


def count_today(conn, kind: str, now: float | None = None) -> int:
    now = time.time() if now is None else now
    row = conn.execute(
        "SELECT COUNT(*) c FROM event_log WHERE kind = ? AND ts >= ?",
        (kind, _today_start_ts(now)),
    ).fetchone()
    return row["c"] if row else 0


_ENGINE = None
_CONN = None


def get_engine():
    """Process-wide send engine: pending confirm state must survive events."""
    global _ENGINE, _CONN
    if _ENGINE is None:
        from hermes_bot import store as _store
        from hermes_bot.sender import enqueue_message
        _CONN = _store.connect()
        try:
            _store.init_db(_CONN)
        except Exception:
            pass
        _ENGINE = _build_send_engine(_CONN, enqueue_message)
    return _ENGINE


def reset_engine() -> None:
    """Tests only: drop the cached engine/connection."""
    global _ENGINE, _CONN
    try:
        if _CONN is not None:
            _CONN.close()
    except Exception:
        pass
    _ENGINE, _CONN = None, None


def handle_event(chat_jid: str, sender_jid: str, content: str,
                 engine=None) -> str:
    """Route one bridge event. Returns the outcome string."""
    from hermes_bot import db as _db
    from hermes_bot import store as _store
    from hermes_bot.sender import enqueue_to_mechat
    from hermes_bot.slash import resolve_self_jid

    text = (content or "").strip()
    if not text:
        return "ignored"

    is_owner = _db.is_message_from_owner(chat_jid, sender_jid)
    self_jid = resolve_self_jid() or ""
    from_self = bool(self_jid) and chat_jid == self_jid

    conn = _store.connect()
    try:
        _store.init_db(conn)
    except Exception:
        pass
    try:
        if engine is None:
            engine = get_engine()

        if is_owner:
            # ── draft N (owner, self-chat) ──
            m = _DRAFT_RE.match(text)
            if m and from_self:
                return _handle_draft(conn, m.group(1), enqueue_to_mechat)
            # ── send NB / yes ──
            if from_self or _has_pending_for(engine, chat_jid, self_jid):
                outcome = engine.handle_event(chat_jid, sender_jid, text)
                if outcome != "ignored":
                    return outcome

        # ── real-time ping over thread messages ──
        if not from_self and not text.startswith("/"):
            return _maybe_ping(conn, chat_jid, text)
        return "ignored"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _has_pending_for(engine, chat_jid: str, self_jid: str) -> bool:
    pending = getattr(engine, "_pending", None)
    return bool(pending) and chat_jid in (pending.get("origin"), self_jid)


def _handle_draft(conn, alias: str, send_self) -> str:
    from hermes_bot import db as _db
    from hermes_bot import draft as _draft
    from hermes_bot import store as _store

    found = _draft.lookup_item(conn, alias)
    if found is None:
        send_self(f"❓ No item `{alias}` — check the latest brief.")
        return "rejected-unknown-ref"
    _brief_no, item = found
    chat_jid = item.get("chat_jid", "")
    try:
        rows = _db.get_chat_messages(chat_jid)
        thread = [f"{r.get('sender', '?')}: {r.get('content', '')}" for r in rows[-20:]]
        chat_name = rows[0].get("chat_name", chat_jid) if rows else chat_jid
    except Exception:
        thread, chat_name = [], chat_jid
    options = _draft.build_drafts(item, thread)["options"]
    _draft.remember_options(conn, alias, chat_jid, chat_name, options)
    try:
        _store.save_draft(conn, chat_jid, options[0]["text"])
    except Exception:
        pass
    send_self(_draft.format_draft_reply(alias, chat_name,
                                        item.get("what", ""), options))
    return "draft-sent"


def _build_send_engine(conn, enqueue_message):
    from hermes_bot import db as _db
    from hermes_bot import draft as _draft
    from hermes_bot import send as _send
    from hermes_bot import store as _store
    from hermes_bot.slash import resolve_self_jid

    def _is_contact(jid: str) -> bool:
        try:
            rows = _db.resolve_contact_by_name("")
            _ = rows
        except Exception:
            pass
        try:
            import sqlite3
            from hermes_bot import config
            con = sqlite3.connect(f"file:{config.MESSAGES_DB}?mode=ro", uri=True)
            try:
                hit = con.execute(
                    "SELECT 1 FROM chats WHERE jid = ?", (jid,)).fetchone()
                if hit:
                    return True
                return con.execute(
                    "SELECT 1 FROM messages WHERE chat_jid = ? LIMIT 1",
                    (jid,)).fetchone() is not None
            finally:
                con.close()
        except Exception:
            return False

    def _owner_wrote(jid: str) -> bool:
        try:
            import sqlite3
            from hermes_bot import config
            con = sqlite3.connect(f"file:{config.MESSAGES_DB}?mode=ro", uri=True)
            try:
                return con.execute(
                    "SELECT 1 FROM messages WHERE chat_jid = ? "
                    "AND is_from_me = 1 LIMIT 1", (jid,)).fetchone() is not None
            finally:
                con.close()
        except Exception:
            return False

    return _send.SendEngine(
        is_owner=_db.is_message_from_owner,
        self_jid=resolve_self_jid() or "",
        enqueue=enqueue_message,
        is_contact=_is_contact,
        owner_wrote_in_chat=_owner_wrote,
        sends_today=lambda: count_today(conn, "send"),
        record_send=lambda: _store.log_event(conn, "send"),
        resolve=lambda ref: _draft.lookup_option(conn, ref),
    )


def _maybe_ping(conn, chat_jid: str, text: str) -> str:
    from hermes_bot import config
    from hermes_bot import pings as _pings
    from hermes_bot import store as _store
    from hermes_bot.sender import enqueue_to_mechat

    try:
        muted = _store.is_muted(conn, chat_jid)
    except Exception:
        muted = False
    owner_tokens = tuple(t for t in (config.OWNER_PHONE,) if t)
    direct = _pings.is_direct_address(text, chat_jid, owner_tokens)
    silence = _owner_silence_s(chat_jid)
    try:
        now = datetime.datetime.now(config.TIMEZONE)
    except Exception:
        now = datetime.datetime.now().astimezone()
    try:
        pings_today = count_today(conn, "ping")
    except Exception:
        pings_today = 0
    fire, reason = _pings.should_ping(direct=direct, owner_silence_s=silence,
                                      muted=muted, pings_today=pings_today,
                                      now=now)
    try:
        conn.close()
    except Exception:
        pass
    if not fire:
        return f"ping-suppressed-{reason}"
    try:
        c2 = _store.connect()
        _store.log_event(c2, "ping", detail=chat_jid[:80])
        c2.close()
    except Exception:
        pass
    chat_name = chat_jid.split("@")[0]
    enqueue_to_mechat(_pings.format_ping(chat_name, text, "?"))
    return "ping-sent"


def _owner_silence_s(chat_jid: str) -> float:
    try:
        import sqlite3
        from hermes_bot import config
        con = sqlite3.connect(f"file:{config.MESSAGES_DB}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT MAX(timestamp) m FROM messages WHERE chat_jid = ? "
                "AND is_from_me = 1", (chat_jid,)).fetchone()
        finally:
            con.close()
        if not row or not row["m"]:
            return float("inf")
        last = datetime.datetime.fromisoformat(str(row["m"]).replace(" ", "T"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=datetime.timezone.utc)
        return max(0.0, (datetime.datetime.now(
            datetime.timezone.utc) - last).total_seconds())
    except Exception:
        return 0.0
