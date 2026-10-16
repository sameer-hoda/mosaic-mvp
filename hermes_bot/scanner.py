"""Incremental scanner: only messages after the cursor, then advance it.

Reads ONLY from /tmp/mosaic-test/messages.db opened read-only (mode=ro).
Never writes to the snapshot; state (items, cursors) lives in mosaic.db.
dry_run=True by default: nothing is ever sent.
"""

import os
import sqlite3

from hermes_bot import extract as ex
from hermes_bot import store as st

SNAPSHOT_PATH = "/tmp/mosaic-test/messages.db"
SNAPSHOT_ENV_VAR = "MOSAIC_SNAPSHOT_PATH"
WHATSAPP_DB_NAME = "whatsapp.db"


def snapshot_path() -> str:
    """Configured snapshot, overridable for live stores (fix 8/10)."""
    return os.getenv(SNAPSHOT_ENV_VAR) or SNAPSHOT_PATH


def archived_jids(snap_conn: sqlite3.Connection) -> set[str]:
    """JIDs archived in the sibling whatsapp.db (whatsmeow_chat_settings).

    Mirrors v4's excluded_jids: archived=1 means the chat is filed away and
    must never yield tasks. Missing file/table means no exclusions.
    """
    try:
        main_file = snap_conn.execute("PRAGMA database_list").fetchall()[0][2]
    except Exception:
        return set()
    if not main_file:
        return set()
    wa = os.path.join(os.path.dirname(main_file), WHATSAPP_DB_NAME)
    if not os.path.exists(wa):
        return set()
    try:
        con = sqlite3.connect(f"file:{wa}?mode=ro", uri=True)
        try:
            return {r[0] for r in con.execute(
                "SELECT chat_jid FROM whatsmeow_chat_settings WHERE archived = 1")}
        finally:
            con.close()
    except Exception:
        return set()


def open_snapshot(path: str | None = None) -> sqlite3.Connection:
    target = path or snapshot_path()
    allowed = {SNAPSHOT_PATH, snapshot_path()}
    if target not in allowed:
        raise ValueError(f"refusing to open non-snapshot db: {target}")
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def list_recent_chats(snap_conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    excluded = archived_jids(snap_conn)
    rows = snap_conn.execute(
        """SELECT jid, name, last_message_time FROM chats
           WHERE jid LIKE '%@g.us'
           ORDER BY last_message_time DESC""",
    ).fetchall()
    return [dict(r) for r in rows if r["jid"] not in excluded][:limit]


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
        junk: dict[str, int] = {}
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
            verdict, reason = ex.keep_or_junk(thread, owner_names)
            if verdict == "junk":
                junk[reason] = junk.get(reason, 0) + 1
                st.log_event(store_conn, "junk", detail=f"{reason}:{chat_jid}")
                st.set_cursor(store_conn, chat_jid, str(rows[-1]["rowid"]))
                continue
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
            named = chat.get("name", "") or ""
            kept = []
            for item in items:
                if ex.is_group_name_item(item.get("what", ""), named):
                    junk["group-name-item"] = junk.get("group-name-item", 0) + 1
                    st.log_event(store_conn, "junk",
                                 detail=f"group-name-item:{chat_jid}")
                else:
                    kept.append(item)
            items = kept
            for item in items:
                st.upsert_item(store_conn, item)
                if not dry_run and send_fn is not None:
                    send_fn(chat_jid, item)
            total_items += len(items)
            per_chat[chat.get("name") or chat_jid] = len(items)
            st.set_cursor(store_conn, chat_jid, str(rows[-1]["rowid"]))
        return {"chats": len(chats), "messages": total_msgs,
                "items": total_items, "llm_calls": llm_calls,
                "junk": junk, "per_chat": per_chat}
    finally:
        snap.close()
