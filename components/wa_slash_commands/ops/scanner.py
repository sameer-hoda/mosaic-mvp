"""
ops/scanner.py — Read WhatsApp bridge DBs, extract groups and messages
"""
from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from ops.models import Group, Message

# Dev fallback: use llm_wiki_sandbox DBs if bridge DBs don't exist locally
_OPS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _OPS_DIR.parent

# Try bridge store first, then fallback to dev data
_DEFAULT_MESSAGES = _PROJECT_ROOT / "store" / "messages.db"
_DEFAULT_WHATSAPP = _PROJECT_ROOT / "store" / "whatsapp.db"
_FALLBACK_MESSAGES = _PROJECT_ROOT.parent / "llm_wiki_sandbox" / "messages.db"
_FALLBACK_WHATSAPP = _PROJECT_ROOT.parent / "llm_wiki_sandbox" / "whatsapp.db"

MESSAGES_DB_PATH = Path(os.getenv("MESSAGES_DB_PATH", _DEFAULT_MESSAGES if _DEFAULT_MESSAGES.exists() else _FALLBACK_MESSAGES))
WHATSAPP_DB_PATH = Path(os.getenv("WHATSAPP_DB_PATH", _DEFAULT_WHATSAPP if _DEFAULT_WHATSAPP.exists() else _FALLBACK_WHATSAPP))


def _attach_dbs(conn: sqlite3.Connection) -> None:
    """Attach whatsapp.db as alias 'wa'."""
    conn.execute(f"ATTACH DATABASE '{WHATSAPP_DB_PATH}' AS wa")


def list_non_archived_groups() -> List[Group]:
    """
    Return all non-archived WhatsApp groups from the bridge DBs.
    T0.2 + T0.3
    """
    conn = sqlite3.connect(str(MESSAGES_DB_PATH))
    conn.row_factory = sqlite3.Row
    _attach_dbs(conn)

    sql = """
    SELECT
        ch.jid,
        ch.name,
        ch.last_message_time,
        COUNT(DISTINCT m.id) as msg_count
    FROM chats ch
    LEFT JOIN messages m ON ch.jid = m.chat_jid
    LEFT JOIN wa.whatsmeow_chat_settings cs ON ch.jid = cs.chat_jid
    WHERE ch.jid LIKE '%@g.us'
      AND (cs.archived IS NULL OR cs.archived = 0)
    GROUP BY ch.jid
    ORDER BY ch.last_message_time DESC
    """
    rows = conn.execute(sql).fetchall()
    conn.close()

    groups = []
    for r in rows:
        ts = _parse_ts(r["last_message_time"])
        groups.append(Group(
            jid=r["jid"],
            name=r["name"] or r["jid"].split("@")[0],
            participant_count=None,  # not available in this query
            whitelisted=False,
            last_message_time=ts,
        ))
    return groups


def get_group_messages(
    group_jid: str,
    since: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Message]:
    """
    Fetch messages for a specific group, optionally filtered by since timestamp.
    Sorted oldest → newest.
    T0.4 + T0.5
    """
    conn = sqlite3.connect(str(MESSAGES_DB_PATH))
    conn.row_factory = sqlite3.Row
    _attach_dbs(conn)

    params = [group_jid]
    since_clause = ""
    if since:
        since_clause = "AND m.timestamp > ?"
        params.append(since.isoformat())

    limit_clause = ""
    if limit:
        limit_clause = f"LIMIT {int(limit)}"

    sql = f"""
    SELECT
        m.id,
        m.chat_jid,
        m.content,
        m.timestamp,
        m.is_from_me,
        m.media_type,
        m.filename,
        COALESCE(c.full_name, c.push_name, c.first_name, c.business_name,
                 SUBSTR(ms.sender_jid, 1, INSTR(ms.sender_jid, '@') - 1)) AS sender_name,
        ms.sender_jid
    FROM messages m
    LEFT JOIN wa.whatsmeow_message_secrets ms
        ON m.id = ms.message_id AND m.chat_jid = ms.chat_jid
    LEFT JOIN wa.whatsmeow_contacts c
        ON ms.sender_jid = c.their_jid
    WHERE m.chat_jid = ?
      AND (m.content IS NOT NULL AND m.content != '')
      {since_clause}
    ORDER BY m.timestamp ASC
    {limit_clause}
    """
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    msgs = []
    for r in rows:
        ts = _parse_ts(r["timestamp"])
        if not ts:
            continue
        sender = "Sameer Hoda" if r["is_from_me"] else (r["sender_name"] or "Unknown")
        body = (r["content"] or "").strip()
        if r["media_type"]:
            extra = f" [Shared {r['media_type']}"
            if r["filename"]:
                extra += f": {r['filename']}"
            extra += "]"
            body += extra
        if not body:
            continue
        msgs.append(Message(
            id=r["id"],
            chat_jid=r["chat_jid"],
            sender_jid=r["sender_jid"] or sender,
            sender_name=sender,
            content=body,
            timestamp=ts,
            is_from_me=bool(r["is_from_me"]),
            media_type=r["media_type"],
            filename=r["filename"],
        ))
    return msgs


def get_latest_message_time(group_jid: str) -> Optional[datetime]:
    conn = sqlite3.connect(str(MESSAGES_DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT MAX(timestamp) as ts FROM messages WHERE chat_jid=?",
        (group_jid,),
    ).fetchone()
    conn.close()
    return _parse_ts(row["ts"]) if row and row["ts"] else None


def _parse_ts(ts) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        ts = ts.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            return None
    return None
