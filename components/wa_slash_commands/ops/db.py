"""
ops/db.py — SQLite persistence layer for WA Ops Platform
"""
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from ops.models import (
    Card, CardStatus, CardMode, CardOrigin,
    WikiPage, WikiPerson, WikiTopic, WikiActionItem,
    ProgressLogEntry, Group, NudgeLogEntry,
)

_OPS_DIR = Path(__file__).resolve().parent
DB_PATH = _OPS_DIR / "ops.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema() -> None:
    """Create all tables if they do not exist."""
    sql = """
    CREATE TABLE IF NOT EXISTS groups (
        jid TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        participant_count INTEGER,
        whitelisted INTEGER DEFAULT 0,
        last_message_time TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS cards (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        group_name TEXT,
        title TEXT NOT NULL,
        context TEXT,
        status TEXT DEFAULT 'backlog',
        mode TEXT DEFAULT 'passive',
        origin TEXT DEFAULT 'ingestion_engine',
        key_people TEXT,
        key_people_confidence REAL,
        eta_raw TEXT,
        eta_parsed TIMESTAMP,
        next_nudge_at TIMESTAMP,
        nudge_count INTEGER DEFAULT 0,
        progress_log TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS wiki (
        group_id TEXT PRIMARY KEY,
        overview TEXT,
        people TEXT,
        topics TEXT,
        action_items TEXT,
        thread_log TEXT,
        last_updated TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS messages_seen (
        message_id TEXT PRIMARY KEY,
        group_id TEXT,
        timestamp TIMESTAMP,
        scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS nudge_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_id TEXT,
        group_id TEXT,
        message_text TEXT,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        response_received INTEGER DEFAULT 0
    );
    """
    conn = _get_conn()
    conn.executescript(sql)
    conn.commit()
    conn.close()


# ── Groups ──────────────────────────────────────────────────────────────────

def save_group(g: Group) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT INTO groups (jid, name, participant_count, whitelisted, last_message_time, created_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(jid) DO UPDATE SET
             name=excluded.name,
             participant_count=excluded.participant_count,
             whitelisted=excluded.whitelisted,
             last_message_time=excluded.last_message_time""",
        (g.jid, g.name, g.participant_count, int(g.whitelisted),
         g.last_message_time.isoformat() if g.last_message_time else None,
         g.created_at.isoformat() if g.created_at else datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_group(jid: str) -> Optional[Group]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM groups WHERE jid=?", (jid,)).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_group(row)


def list_groups(whitelisted_only: bool = False) -> List[Group]:
    conn = _get_conn()
    if whitelisted_only:
        rows = conn.execute("SELECT * FROM groups WHERE whitelisted=1 ORDER BY name").fetchall()
    else:
        rows = conn.execute("SELECT * FROM groups ORDER BY name").fetchall()
    conn.close()
    return [_row_to_group(r) for r in rows]


def _row_to_group(row: sqlite3.Row) -> Group:
    return Group(
        jid=row["jid"],
        name=row["name"],
        participant_count=row["participant_count"],
        whitelisted=bool(row["whitelisted"]),
        last_message_time=_parse_ts(row["last_message_time"]),
        created_at=_parse_ts(row["created_at"]),
    )


# ── Cards ───────────────────────────────────────────────────────────────────

def save_card(c: Card) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT INTO cards (id, group_id, group_name, title, context, status, mode, origin,
             key_people, key_people_confidence, eta_raw, eta_parsed, next_nudge_at,
             nudge_count, progress_log, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             group_name=excluded.group_name,
             title=excluded.title,
             context=excluded.context,
             status=excluded.status,
             mode=excluded.mode,
             key_people=excluded.key_people,
             key_people_confidence=excluded.key_people_confidence,
             eta_raw=excluded.eta_raw,
             eta_parsed=excluded.eta_parsed,
             next_nudge_at=excluded.next_nudge_at,
             nudge_count=excluded.nudge_count,
             progress_log=excluded.progress_log,
             updated_at=excluded.updated_at""",
        (c.id, c.group_id, c.group_name, c.title, c.context,
         c.status.value, c.mode.value, c.origin.value,
         json.dumps(c.key_people), c.key_people_confidence,
         c.eta_raw,
         c.eta_parsed.isoformat() if c.eta_parsed else None,
         c.next_nudge_at.isoformat() if c.next_nudge_at else None,
         c.nudge_count,
          json.dumps([e.model_dump(mode="json") for e in c.progress_log]),
         c.created_at.isoformat() if c.created_at else datetime.now().isoformat(),
         datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_card(card_id: str) -> Optional[Card]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_card(row)


def list_cards(group_id: Optional[str] = None, status: Optional[str] = None) -> List[Card]:
    conn = _get_conn()
    q = "SELECT * FROM cards WHERE 1=1"
    params = []
    if group_id:
        q += " AND group_id=?"
        params.append(group_id)
    if status:
        q += " AND status=?"
        params.append(status)
    q += " ORDER BY created_at DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [_row_to_card(r) for r in rows]


def get_active_cards() -> List[Card]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM cards WHERE mode='active' AND status='in_progress' ORDER BY next_nudge_at"
    ).fetchall()
    conn.close()
    return [_row_to_card(r) for r in rows]


def delete_card(card_id: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM cards WHERE id=?", (card_id,))
    conn.commit()
    conn.close()


def _row_to_card(row: sqlite3.Row) -> Card:
    return Card(
        id=row["id"],
        group_id=row["group_id"],
        group_name=row["group_name"],
        title=row["title"],
        context=row["context"],
        status=CardStatus(row["status"]),
        mode=CardMode(row["mode"]),
        origin=CardOrigin(row["origin"]),
        key_people=json.loads(row["key_people"] or "[]"),
        key_people_confidence=row["key_people_confidence"] or 0.0,
        eta_raw=row["eta_raw"],
        eta_parsed=_parse_ts(row["eta_parsed"]),
        next_nudge_at=_parse_ts(row["next_nudge_at"]),
        nudge_count=row["nudge_count"],
        progress_log=[ProgressLogEntry(**e) for e in json.loads(row["progress_log"] or "[]")],
        created_at=_parse_ts(row["created_at"]),
        updated_at=_parse_ts(row["updated_at"]),
    )


# ── Wiki ────────────────────────────────────────────────────────────────────

def save_wiki(w: WikiPage) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT INTO wiki (group_id, overview, people, topics, action_items, thread_log, last_updated)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(group_id) DO UPDATE SET
             overview=excluded.overview,
             people=excluded.people,
             topics=excluded.topics,
             action_items=excluded.action_items,
             thread_log=excluded.thread_log,
             last_updated=excluded.last_updated""",
        (w.group_id, w.overview,
         json.dumps([p.model_dump(mode="json") for p in w.people]),
         json.dumps([t.model_dump(mode="json") for t in w.topics]),
         json.dumps([a.model_dump(mode="json") for a in w.action_items]),
         json.dumps(w.thread_log),
         datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_wiki(group_id: str) -> Optional[WikiPage]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM wiki WHERE group_id=?", (group_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_wiki(row)


def _row_to_wiki(row: sqlite3.Row) -> WikiPage:
    return WikiPage(
        group_id=row["group_id"],
        overview=row["overview"] or "",
        people=[WikiPerson(**p) for p in json.loads(row["people"] or "[]")],
        topics=[WikiTopic(**t) for t in json.loads(row["topics"] or "[]")],
        action_items=[WikiActionItem(**a) for a in json.loads(row["action_items"] or "[]")],
        thread_log=json.loads(row["thread_log"] or "[]"),
        last_updated=_parse_ts(row["last_updated"]),
    )


# ── Messages Seen ───────────────────────────────────────────────────────────

def is_message_seen(message_id: str) -> bool:
    conn = _get_conn()
    row = conn.execute("SELECT 1 FROM messages_seen WHERE message_id=?", (message_id,)).fetchone()
    conn.close()
    return row is not None


def mark_messages_seen(message_ids: List[str], group_id: str) -> None:
    if not message_ids:
        return
    conn = _get_conn()
    now = datetime.now().isoformat()
    for mid in message_ids:
        conn.execute(
            "INSERT OR IGNORE INTO messages_seen (message_id, group_id, timestamp, scanned_at) VALUES (?, ?, ?, ?)",
            (mid, group_id, now, now),
        )
    conn.commit()
    conn.close()


def get_last_seen_timestamp(group_id: str) -> Optional[datetime]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT MAX(timestamp) as ts FROM messages_seen WHERE group_id=?", (group_id,)
    ).fetchone()
    conn.close()
    return _parse_ts(row["ts"]) if row and row["ts"] else None


# ── Nudge Log ───────────────────────────────────────────────────────────────

def log_nudge(entry: NudgeLogEntry) -> int:
    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO nudge_log (card_id, group_id, message_text, sent_at, response_received)
           VALUES (?, ?, ?, ?, ?)""",
        (entry.card_id, entry.group_id, entry.message_text,
         entry.sent_at.isoformat() if entry.sent_at else datetime.now().isoformat(),
         int(entry.response_received)),
    )
    conn.commit()
    nid = cur.lastrowid
    conn.close()
    return nid


def list_nudges(card_id: Optional[str] = None, group_id: Optional[str] = None) -> List[NudgeLogEntry]:
    conn = _get_conn()
    q = "SELECT * FROM nudge_log WHERE 1=1"
    params = []
    if card_id:
        q += " AND card_id=?"
        params.append(card_id)
    if group_id:
        q += " AND group_id=?"
        params.append(group_id)
    q += " ORDER BY sent_at DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [_row_to_nudge(r) for r in rows]


def _row_to_nudge(row: sqlite3.Row) -> NudgeLogEntry:
    return NudgeLogEntry(
        id=row["id"],
        card_id=row["card_id"],
        group_id=row["group_id"],
        message_text=row["message_text"],
        sent_at=_parse_ts(row["sent_at"]),
        response_received=bool(row["response_received"]),
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

def _parse_ts(ts) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        # Handle SQLite timestamps with or without timezone
        ts = ts.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            return None
    return None
