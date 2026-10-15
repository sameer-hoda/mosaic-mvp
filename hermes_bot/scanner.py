"""Incremental scanner: only messages after the cursor, then advance it.

Reads ONLY from /tmp/mosaic-test/messages.db opened read-only (mode=ro).
Never writes to the snapshot; state (items, cursors) lives in mosaic.db.
dry_run=True by default: nothing is ever sent.
"""

import sqlite3

from hermes_bot import extract as ex
from hermes_bot import store as st

SNAPSHOT_PATH = "/tmp/mosaic-test/messages.db"


def open_snapshot(path: str | None = None) -> sqlite3.Connection:
    target = path or SNAPSHOT_PATH
    if target != SNAPSHOT_PATH:
        raise ValueError(f"refusing to open non-snapshot db: {target}")
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def list_recent_chats(snap_conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = snap_conn.execute(
        """SELECT jid, name, last_message_time FROM chats
           WHERE jid LIKE '%@g.us'
           ORDER BY last_message_time DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_new_messages(snap_conn: sqlite3.Connection, chat_jid: str,
                       cursor: str, limit: int = 500) -> list[dict]:
    try:
        after = int(cursor) if cursor else 0
    except ValueError:
        after = 0
    rows = snap_conn.execute(
        """SELECT rowid, id, chat_jid, sender, content, timestamp, is_from_me
           FROM messages WHERE chat_jid = ? AND rowid > ?
           ORDER BY rowid ASC LIMIT ?""",
        (chat_jid, after, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _to_thread(chat_jid: str, chat_name: str, rows: list[dict]) -> dict:
    return {
        "chat_jid": chat_jid,
        "chat": chat_name or chat_jid,
        "msgs": [
            {"id": r["id"], "sender": r["sender"] or "",
             "body": r["content"] or "", "ts": r["timestamp"] or "",
             "from_me": bool(r["is_from_me"]), "rowid": r["rowid"]}
            for r in rows
        ],
    }


def scan(store_conn: sqlite3.Connection, snapshot_path: str | None = None,
         limit_chats: int = 20, llm_fn=None, dry_run: bool = True,
         send_fn=None, owner_names: list[str] | None = None) -> dict:
    """Scan new messages in the most recent chats.

    llm_fn(thread) -> {"items": [...]} is called ONLY for gated threads.
    Items are normalized + upserted with stable ids; the cursor advances
    past every consumed message. send_fn is only invoked when
    dry_run=False and a send_fn was supplied (default: never sends).
    """
    owner_names = owner_names or []
    snap = open_snapshot(snapshot_path)
    try:
        chats = list_recent_chats(snap, limit_chats)
        total_msgs = 0
        total_items = 0
        llm_calls = 0
        per_chat: dict[str, int] = {}
        for chat in chats:
            chat_jid = chat["jid"]
            if st.is_muted(store_conn, chat_jid):
                continue
            cursor = st.get_cursor(store_conn, chat_jid)
            rows: list[dict] = []
            while True:
                batch = fetch_new_messages(snap, chat_jid, cursor)
                if not batch:
                    break
                rows.extend(batch)
                cursor = str(batch[-1]["rowid"])
                if len(batch) < 500:
                    break
            if not rows:
                continue
            total_msgs += len(rows)
            thread = ex.enrich_thread(_to_thread(chat_jid, chat.get("name", ""), rows),
                                      owner_names)
            items: list[dict] = []
            if ex.should_call_llm(thread) and llm_fn is not None:
                llm_calls += 1
                got = llm_fn(thread) or {}
                for raw in got.get("items", [])[:4]:
                    norm = ex.normalize_item(raw)
                    if norm:
                        norm["chat_jid"] = chat_jid
                        items.append(norm)
            else:
                got = ex.heuristic_extract(thread) or {}
                for raw in got.get("items", [])[:4]:
                    raw = dict(raw)
                    raw["chat_jid"] = chat_jid
                    items.append(raw)
            for item in items:
                st.upsert_item(store_conn, item)
                if not dry_run and send_fn is not None:
                    send_fn(chat_jid, item)
            total_items += len(items)
            per_chat[chat.get("name") or chat_jid] = len(items)
            st.set_cursor(store_conn, chat_jid, str(rows[-1]["rowid"]))
        return {"chats": len(chats), "messages": total_msgs,
                "items": total_items, "llm_calls": llm_calls,
                "per_chat": per_chat}
    finally:
        snap.close()
